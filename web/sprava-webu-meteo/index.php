<?php
// Správa webu Meteogramy.cz: anonymní návštěvnost (api/stats.php), oblíbené stanice a stav aktualizací.
// Jen pro čtení, nic nemaže ani nespouští.
//
// Přihlášení kódem z e-mailu. Adresa leží MIMO public_html v souboru /meteogramy-admin.txt
// (o úroveň výš než public_html, nahrává se ručně, ne z gitu). Kód platí 10 minut a jen jednou,
// po 5 chybách propadne; poslat jde nejvýš jednou za minutu a 5× za hodinu. Přihlášení platí 30 dní.
// Stav přihlášení je v api/stats/auth.json (bez přímého přístupu); jeho smazáním přes SFTP se zruší
// všechna přihlášení i limity odesílání. Kód se nikam neukládá, jen jeho otisk.
// Lokálně (php -S) se e-mail neposílá, kód se vypíše do konzole serveru.

const CODE_TTL = 600;
const CODE_TRIES = 5;
const SEND_GAP = 60;
const SEND_PER_HOUR = 5;
const SESSION_TTL = 30 * 86400;

date_default_timezone_set('Europe/Prague');
$ROOT = dirname(__DIR__);
$STATS = "$ROOT/api/stats";
$CONFIG = dirname(__DIR__, 2) . '/meteogramy-admin.txt';
// lokální test (php -S na tomto počítači): e-mail se neposílá a jde se přihlásit bez kódu; na hostingu nikdy
$DEV = PHP_SAPI === 'cli-server' && in_array($_SERVER['REMOTE_ADDR'] ?? '', ['127.0.0.1', '::1'], true);
$HTTPS = ($_SERVER['HTTPS'] ?? '') === 'on' || ($_SERVER['HTTP_X_FORWARDED_PROTO'] ?? '') === 'https';
$COOKIE = $HTTPS ? '__Host-mgadmin' : 'mgadmin';
$NONCE = base64_encode(random_bytes(12));

header('Content-Type: text/html; charset=utf-8');
header('Cache-Control: no-store');
header('X-Robots-Tag: noindex, nofollow');
header('Referrer-Policy: same-origin');
header('X-Content-Type-Options: nosniff');
header("Content-Security-Policy: default-src 'none'; img-src 'self' data:; connect-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'nonce-$NONCE'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'");

function e($s) { return htmlspecialchars((string)$s, ENT_QUOTES, 'UTF-8'); }

function read_json($file) {
    $data = is_file($file) ? json_decode((string)@file_get_contents($file), true) : null;
    return is_array($data) ? $data : [];
}

// zamkne soubor, $fn upraví data, výsledek zapíše zpět
function locked($file, $fn) {
    if (!is_dir(dirname($file))) mkdir(dirname($file), 0755, true);
    $fp = fopen($file, 'c+');
    if (!$fp || !flock($fp, LOCK_EX)) throw new RuntimeException('Nelze zamknout stav přihlášení.');
    $data = json_decode(stream_get_contents($fp), true);
    if (!is_array($data)) $data = [];
    $result = $fn($data);
    ftruncate($fp, 0);
    rewind($fp);
    fwrite($fp, json_encode($data));
    fflush($fp);
    flock($fp, LOCK_UN);
    fclose($fp);
    return $result;
}

function admin_email($file) {
    $mail = is_readable($file) ? trim((string)file_get_contents($file)) : '';
    return filter_var($mail, FILTER_VALIDATE_EMAIL) ? $mail : '';
}

function masked($mail) {
    [$user, $domain] = explode('@', $mail, 2);
    return mb_substr($user, 0, 1) . '…@' . $domain;
}

function redirect($query = '') {
    header('Location: ' . strtok($_SERVER['REQUEST_URI'], '?') . ($query ? "?$query" : ''), true, 303);
    exit;
}

function session_user($stats, $cookie) {
    $token = $_COOKIE[$cookie] ?? '';
    if (!preg_match('/^[a-f0-9]{64}$/', $token)) return false;
    $auth = read_json("$stats/auth.json");
    $s = $auth['sessions'][hash('sha256', $token)] ?? null;
    return $s && $s['exp'] > time();
}

function set_cookie($name, $value, $exp, $secure) {
    setcookie($name, $value, ['expires' => $exp, 'path' => '/', 'secure' => $secure, 'httponly' => true, 'samesite' => 'Strict']);
}

// --- akce (POST) ---------------------------------------------------------------------------------
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    $origin = $_SERVER['HTTP_ORIGIN'] ?? '';
    if ($origin !== '' && parse_url($origin, PHP_URL_HOST) !== strtok($_SERVER['HTTP_HOST'] ?? '', ':')) {
        http_response_code(403);
        exit('Zakázáno.');
    }
    $action = $_POST['akce'] ?? '';
    $authFile = "$STATS/auth.json";
    $now = time();

    if ($action === 'poslat') {
        $mail = admin_email($CONFIG);
        if ($mail === '') redirect('chyba=adresa');
        $code = str_pad((string)random_int(0, 999999), 6, '0', STR_PAD_LEFT);
        $result = locked($authFile, function (&$a) use ($now, $code) {
            $sends = array_values(array_filter($a['sends'] ?? [], fn($t) => $t > $now - 3600));
            if ($sends && end($sends) > $now - SEND_GAP) return 'pockej';
            if (count($sends) >= SEND_PER_HOUR) return 'limit';
            $sends[] = $now;
            $salt = bin2hex(random_bytes(16));
            $a['sends'] = $sends;
            $a['code'] = ['salt' => $salt, 'hash' => hash('sha256', $salt . $code), 'exp' => $now + CODE_TTL, 'tries' => 0];
            return 'ok';
        });
        // kód před chvílí odešel a pořád platí: rovnou na jeho zadání
        if ($result === 'pockej') redirect('krok=kod&chyba=pockej');
        if ($result !== 'ok') redirect("chyba=$result");
        $body = "Kód pro přihlášení do správy webu Meteogramy.cz:\n\n$code\n\nPlatí 10 minut. "
              . "Pokud jste o kód nežádali, zprávu ignorujte; bez kódu se nikdo nepřihlásí.\n";
        if ($DEV) {
            error_log("[správa webu] kód pro $mail: $code");
            $sent = true;
        } else {
            $headers = "From: Meteogramy.cz <noreply@meteogramy.cz>\r\nContent-Type: text/plain; charset=UTF-8\r\n"
                     . "Content-Transfer-Encoding: 8bit\r\nAuto-Submitted: auto-generated";
            $sent = mail($mail, '=?UTF-8?B?' . base64_encode("Přihlašovací kód $code – Meteogramy.cz") . '?=',
                         $body, $headers, '-fnoreply@meteogramy.cz');
        }
        redirect($sent ? 'krok=kod' : 'chyba=mail');
    }

    if ($action === 'lokalne' && $DEV) {
        $token = bin2hex(random_bytes(32));
        locked($authFile, function (&$a) use ($now, $token) {
            $a['sessions'][hash('sha256', $token)] = ['exp' => $now + SESSION_TTL, 'at' => $now, 'ua' => 'lokální test'];
        });
        set_cookie($COOKIE, $token, $now + SESSION_TTL, $HTTPS);
        redirect();
    }

    if ($action === 'overit') {
        $code = preg_replace('/\D/', '', (string)($_POST['kod'] ?? ''));
        $token = bin2hex(random_bytes(32));
        $ua = substr((string)($_SERVER['HTTP_USER_AGENT'] ?? ''), 0, 200);
        $result = locked($authFile, function (&$a) use ($now, $code, $token, $ua) {
            $c = $a['code'] ?? null;
            if (!$c || $c['exp'] < $now || $c['tries'] >= CODE_TRIES) { unset($a['code']); return 'vyprsel'; }
            if (!hash_equals($c['hash'], hash('sha256', $c['salt'] . $code))) {
                $a['code']['tries']++;
                if ($a['code']['tries'] >= CODE_TRIES) { unset($a['code']); return 'vyprsel'; }
                return 'spatny';
            }
            unset($a['code']);
            $sessions = array_filter($a['sessions'] ?? [], fn($s) => $s['exp'] > $now);
            $sessions[hash('sha256', $token)] = ['exp' => $now + SESSION_TTL, 'at' => $now, 'ua' => $ua];
            $a['sessions'] = $sessions;
            return 'ok';
        });
        if ($result !== 'ok') redirect($result === 'spatny' ? 'krok=kod&chyba=spatny' : 'chyba=vyprsel');
        set_cookie($COOKIE, $token, $now + SESSION_TTL, $HTTPS);
        redirect();
    }

    if ($action === 'odhlasit' || $action === 'odhlasit-vse') {
        $token = (string)($_COOKIE[$COOKIE] ?? '');
        if (session_user($STATS, $COOKIE)) {
            locked($authFile, function (&$a) use ($action, $token) {
                if ($action === 'odhlasit-vse') $a['sessions'] = [];
                else unset($a['sessions'][hash('sha256', $token)]);
            });
        }
        set_cookie($COOKIE, '', 1, $HTTPS);
        redirect('odhlaseno=1');
    }
    redirect();
}

$logged = session_user($STATS, $COOKIE);

const RANGES = [7 => '7 dní', 30 => '30 dní', 90 => '90 dní', 365 => 'Rok'];

function slugify($t) {
    $t = iconv('UTF-8', 'ASCII//TRANSLIT', $t);
    return trim(preg_replace('/[^a-z0-9]+/', '-', strtolower($t)), '-');
}

// seřazený rozpis [[popisek, počet], ...]
function ranked($map, $label) {
    arsort($map);
    $out = [];
    foreach ($map as $k => $v) $out[] = [$label((string)$k), $v];
    return $out;
}

// Data přehledu za posledních $span dní (a stejně dlouhé předchozí období pro srovnání).
function dashboard($span, $ROOT, $STATS) {
    $today = strtotime('today');
    $cur = ['pv' => 0, 'uv' => 0, 'app' => 0, 'pages' => [], 'refs' => [], 'dev' => [], 'br' => [], 'os' => [], 'consent' => [], 'hours' => array_fill(0, 24, 0)];
    $prev = ['pv' => 0, 'uv' => 0];
    $daily = [];
    for ($i = 2 * $span - 1; $i >= 0; $i--) {
        $ts = strtotime("-$i days", $today);
        $d = read_json("$STATS/days/" . date('Y-m-d', $ts) . '.json');
        if ($i >= $span) {
            $prev['pv'] += $d['pv'] ?? 0;
            $prev['uv'] += $d['uv'] ?? 0;
            continue;
        }
        $daily[] = [date('Y-m-d', $ts), $d['uv'] ?? 0, $d['pv'] ?? 0];
        foreach (['pv', 'uv', 'app'] as $k) $cur[$k] += $d[$k] ?? 0;
        foreach (['pages', 'refs', 'dev', 'br', 'os', 'consent'] as $k)
            foreach ($d[$k] ?? [] as $key => $v) $cur[$k][$key] = ($cur[$k][$key] ?? 0) + $v;
        foreach ($d['hours'] ?? [] as $h => $v) $cur['hours'][$h] += $v;
    }
    $todayStats = read_json("$STATS/days/" . date('Y-m-d') . '.json');

    // názvy stanic a pohoří z posledního běhu modelu
    $run = read_json("$ROOT/data/index.json")['runs'][0] ?? '';
    $names = ['/' => 'Úvod', '/snehove-zpravodajstvi/' => 'Sněhové zpravodajství', '/lavinova-predpoved/' => 'Lavinová předpověď',
              '/historie-zim/' => 'Historie zim', '/o-projektu/' => 'O projektu', '/ochrana-soukromi/' => 'Ochrana soukromí'];
    $places = preg_match('/^20\d{8}$/', $run) ? (read_json("$ROOT/data/$run/index.json")['places'] ?? []) : [];
    $rangeOf = $subOf = [];
    foreach ($places as $p) {
        $names["/{$p['id']}/"] = $p['name'];
        $rangeOf[$p['id']] = $p['range'];
        $names['/' . slugify($p['range']) . '/'] ??= $p['range'];
        $subOf["/{$p['id']}/"] = $p['range'];
        $subOf['/' . slugify($p['range']) . '/'] = 'pohoří';
    }
    $refNames = ['(přímo)' => 'Přímo, záložky a aplikace', 'google' => 'Google', 'seznam.cz' => 'Seznam', 'bing.com' => 'Bing',
                 'facebook.com' => 'Facebook', 'instagram.com' => 'Instagram', 'x.com' => 'X (Twitter)', 'chatgpt.com' => 'ChatGPT',
                 'duckduckgo.com' => 'DuckDuckGo', 'ostatní' => 'Ostatní'];
    $devNames = ['mobil' => 'Mobil', 'tablet' => 'Tablet', 'počítač' => 'Počítač'];

    // oblíbené stanice (api/oblibene.php, posledních 30 dní)
    $fav = [];
    foreach (read_json("$STATS/views.json") as $d => $counts)
        if ($d > gmdate('Y-m-d', time() - 30 * 86400)) foreach ($counts as $id => $v) $fav[$id] = ($fav[$id] ?? 0) + $v;

    // stav aktualizací: kdy naposledy proběhly a jestli nejsou starší než jejich limit
    $status = [];
    $add = function ($name, $ts, $limit, $ok, $desc) use (&$status) {
        $status[] = ['name' => $name, 'ts' => $ts, 'fine' => $ok && $ts && time() - $ts <= $limit, 'desc' => $desc];
    };
    if (preg_match('/^(\d{4})(\d\d)(\d\d)(\d\d)$/', $run, $m))
        $add('Model ALADIN', gmmktime((int)$m[4], 0, 0, (int)$m[2], (int)$m[3], (int)$m[1]), 12 * 3600, true, 'publikovaný běh');
    else
        $add('Model ALADIN', 0, 0, false, 'žádný publikovaný běh');
    $cron = read_json("$STATS/cron-dispatch.json");
    $add('Hodinový cron', (int)($cron['last'] ?? 0), 90 * 60, true, 'spuštění aktualizace');
    foreach (['alerts' => 'Výstrahy ČHMÚ', 'avalanche' => 'Lavinová předpověď', 'rescue' => 'Horská služba', 'snow' => 'Výška sněhu'] as $f => $label) {
        $j = read_json("$ROOT/data/$f.json");
        $ok = ($j['ok'] ?? true) !== false;
        $add($label, isset($j['retrieved_at']) ? strtotime($j['retrieved_at']) : 0, 3 * 3600, $ok, $ok ? 'načteno' : 'zdroj nedostupný');
    }

    return [
        'span' => $span, 'now' => time(),
        'uv' => $cur['uv'], 'pv' => $cur['pv'], 'app' => $cur['app'], 'prev' => $prev,
        'today' => ['uv' => $todayStats['uv'] ?? 0, 'pv' => $todayStats['pv'] ?? 0],
        'mobile' => ($cur['dev']['mobil'] ?? 0) + ($cur['dev']['tablet'] ?? 0),
        'daily' => $daily, 'hours' => $cur['hours'],
        // stránky: [název, počet, pohoří stanice nebo „pohoří“ u stránky pohoří]
        'pages' => array_map(fn($x) => [$names[$x[0]] ?? $x[0], $x[1], $subOf[$x[0]] ?? ''], ranked($cur['pages'], fn($k) => $k)),
        'refs' => ranked($cur['refs'], fn($k) => $refNames[$k] ?? $k),
        'dev' => ranked($cur['dev'], fn($k) => $devNames[$k] ?? $k),
        'br' => ranked($cur['br'], fn($k) => $k === 'jiný' ? 'Jiný' : $k),
        'os' => ranked($cur['os'], fn($k) => $k === 'jiný' ? 'Jiný' : $k),
        // oblíbené stanice i s pohořím: [název, počet, pohoří]
        'fav' => array_map(fn($x) => [$names["/{$x[0]}/"] ?? $x[0], $x[1], $rangeOf[$x[0]] ?? ''], ranked($fav, fn($id) => $id)),
        'consent' => (object)$cur['consent'],
        'status' => $status,
    ];
}

// data pro přehled načítá stránka na pozadí (kvůli skeletonům a přepínání období bez nového načtení)
if (isset($_GET['data'])) {
    header('Content-Type: application/json; charset=utf-8');
    if (!$logged) { http_response_code(401); exit('{}'); }
    $span = (int)($_GET['dny'] ?? 30);
    echo json_encode(dashboard(isset(RANGES[$span]) ? $span : 30, $ROOT, $STATS), JSON_UNESCAPED_UNICODE);
    exit;
}

// ikony ve stylu webu (tah 2,4 px na mřížce 24 px, viz ICONS v index.html)
const ICONS = [
    'user' => '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
    'eye' => '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    'clock' => '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    'phone' => '<rect x="6" y="2.5" width="12" height="19" rx="3"/><path d="M11 18h2"/>',
    'chart' => '<path d="M3 21h18M6 17v-5M11 17V6M16 17v-8M21 17v-3"/>',
    'page' => '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>',
    'link' => '<path d="M10 13a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1 1"/><path d="M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1-1"/>',
    'globe' => '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
    'screen' => '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/>',
    'star' => '<path d="M12 3l2.8 5.7 6.2.9-4.5 4.4 1 6.2L12 17.3 6.5 20.2l1-6.2L3 9.6l6.2-.9z"/>',
    'pulse' => '<path d="M3 12h4l3-8 4 16 3-8h4"/>',
    'cookie' => '<path d="M12 3a9 9 0 1 0 9 9 4 4 0 0 1-4.5-4.5A4 4 0 0 1 12 3z"/><path d="M8.5 11.5h.01M12 16h.01M15.5 14h.01M9 15.5h.01"/>',
];
function icon($k) {
    return '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' . ICONS[$k] . '</svg>';
}
function head($ic, $title, $note = '') {
    return '<h2 class="lb">' . icon($ic) . '<span>' . e($title) . '</span>' . ($note ? '<small>' . e($note) . '</small>' : '') . '</h2>';
}
function tile($slot, $ic, $label) {
    return '<div class="st" data-tile="' . $slot . '"><div class="lb">' . icon($ic) . '<span>' . e($label) . '</span></div>'
         . '<b><i class="sk" style="width:46%;height:22px"></i></b><div class="sx"><i class="sk" style="width:72%;height:10px"></i></div></div>';
}

// --- stránka ----------------------------------------------------------------------------------------
$errors = ['adresa' => 'Na hostingu chybí soubor s adresou pro kódy (meteogramy-admin.txt mimo public_html).',
           'pockej' => 'Kód už odešel před chvílí. Zadejte ho, nový jde poslat nejdřív za minutu.', 'limit' => 'Za poslední hodinu odešlo příliš mnoho kódů. Zkuste to později.',
           'mail' => 'E-mail se nepodařilo odeslat.', 'vyprsel' => 'Kód vypršel nebo už byl použit. Pošlete si nový.',
           'spatny' => 'Kód nesouhlasí. Zkuste to znovu.'];
$error = $errors[$_GET['chyba'] ?? ''] ?? '';
$step = ($_GET['krok'] ?? '') === 'kod' ? 'kod' : 'start';
$mail = admin_email($CONFIG);
$span = (int)($_GET['dny'] ?? 30);
if (!isset(RANGES[$span])) $span = 30;
?>
<!doctype html>
<html lang="cs">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="robots" content="noindex, nofollow">
<meta name="referrer" content="same-origin">
<meta name="theme-color" content="#f8f8fa">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Správa webu">
<title>Správa webu – Meteogramy.cz</title>
<link rel="icon" href="/favicon.svg?v=4" type="image/svg+xml">
<link rel="apple-touch-icon" href="/assets/apple-touch-icon.png?v=4">
<style>
/* Vzhled, pohyb a skeletony převzaté z webu (web/index.html): karty .hc, dlaždice .st, popisky .lb, tooltip #tip, .sk. */
:root { --bg: #f8f8fa; --paper: #fff; --ink: #15191f; --ink-2: #4a515b; --muted: #858c96; --line: #dde1e7; --soft: #f5f7fa;
  --accent: #1f5fbf; --accent-bg: #e8f0fc; --good: #2e9b3e; --warn: #c4580f; --gap: 16px; --ease: cubic-bezier(.2, .8, .2, 1); color-scheme: light; }
[hidden] { display: none !important; }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink); -webkit-font-smoothing: antialiased; -webkit-tap-highlight-color: transparent;
  font: 15px/1.45 -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", Roboto, sans-serif; }
h1, h2 { text-wrap: balance; }
.wrap { max-width: 1180px; margin: 0 auto; padding: max(24px, env(safe-area-inset-top)) max(24px, env(safe-area-inset-right)) 56px max(24px, env(safe-area-inset-left)); }

/* hlavička jako logo webu */
header { display: flex; align-items: center; gap: 14px 20px; flex-wrap: wrap; margin-bottom: 24px; }
.brand { display: flex; align-items: center; gap: 11px; margin-right: auto; color: inherit; text-decoration: none; padding: 0 6px 0 0; }
.gl { position: relative; display: block; flex: 0 0 auto; border-radius: 22.75%;
  box-shadow: 0 1px 2px rgba(16,60,120,.16), 0 4px 12px rgba(16,60,120,.14); }
.gl > img { display: block; border-radius: inherit; }
.gl::after { content: ""; position: absolute; inset: 0; border-radius: inherit; pointer-events: none;
  background: linear-gradient(180deg, rgba(255,255,255,.16), rgba(255,255,255,0) 42%);
  box-shadow: inset 0 0 0 .5px rgba(255,255,255,.4), inset 0 1.5px 1px -.5px rgba(255,255,255,.85),
    inset 1px 0 1px -.5px rgba(255,255,255,.35), inset 0 -1px 1px -.5px rgba(255,255,255,.45); }
.brand b { display: block; font-weight: 700; font-size: 17px; letter-spacing: -.01em; line-height: 1.15; }
.brand small { display: block; font-size: 12px; color: var(--muted); line-height: 1.3; margin-top: 1px; }

/* přepínač období: posuvný jezdec jako segmentový ovladač v iOS */
.seg { position: relative; display: inline-flex; background: rgba(118,118,128,.12); border-radius: 10px; padding: 2px; }
.seg .thumb { position: absolute; top: 2px; bottom: 2px; left: 0; width: 0; border-radius: 8px; background: var(--paper);
  box-shadow: 0 3px 8px rgba(0,0,0,.12), 0 3px 1px rgba(0,0,0,.04), 0 0 0 .5px rgba(0,0,0,.04); transition: transform .35s var(--ease), width .35s var(--ease); }
.seg a { position: relative; z-index: 1; padding: 6px 15px; color: var(--ink); text-decoration: none; font-size: 13px; font-weight: 500;
  border-radius: 8px; transition: opacity .15s; text-align: center; }
.seg a[aria-current] { font-weight: 600; }
.seg a:active { opacity: .6; }

/* dlaždice jako „nej“ na úvodu webu (.hl .st) */
.hl { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: var(--gap); margin-bottom: var(--gap); }
.st { background: var(--paper); border-radius: 16px; padding: 13px 16px 14px; min-width: 0; display: flex; flex-direction: column;
  box-shadow: 0 0 0 1px rgba(0,0,0,.04), 0 1px 2px rgba(0,0,0,.04); transition: box-shadow .4s, transform .4s var(--ease); }
.lb { display: flex; align-items: center; gap: 5px; margin: 0; font-size: 10.5px; font-weight: 600; letter-spacing: .05em; text-transform: uppercase; color: #8e8e93; white-space: nowrap; }
.lb svg { display: block; flex: 0 0 auto; }
.lb span { text-box: trim-both cap alphabetic; }
.lb small { font-size: 11px; font-weight: 500; letter-spacing: 0; text-transform: none; color: #aeaeb2; text-box: trim-both cap alphabetic; }
.st b { display: flex; align-items: center; gap: 5px; margin-top: 4px; font-size: 24px; font-weight: 600; letter-spacing: -.015em;
  font-variant-numeric: tabular-nums; white-space: nowrap; line-height: 1.15; min-height: 28px; }
.st b span { font-size: 13px; font-weight: 500; color: #6e6e73; letter-spacing: 0; }
.st .sx { display: block; margin-top: 3px; font-size: 12px; color: #6e6e73; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-height: 17px; }
.st .sx em { font-style: normal; color: var(--ink); font-weight: 600; }
@media (hover: hover) { .st:hover { transform: translateY(-2px); box-shadow: 0 0 0 1px rgba(0,0,0,.07), 0 6px 18px rgba(0,0,0,.08); } }

/* karty sekcí jako karta stanice na úvodu (.hc) */
.card { background: var(--paper); border-radius: 22px; padding: 18px 20px 16px; min-width: 0;
  box-shadow: 0 0 0 1px rgba(0,0,0,.04), 0 1px 2px rgba(0,0,0,.04), 0 12px 36px rgba(0,0,0,.07); }
.card > .lb { margin-bottom: 14px; }
.g2, .g3 { display: grid; gap: var(--gap); margin-top: var(--gap); }
.g2 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.g3 { grid-template-columns: repeat(3, minmax(0, 1fr)); }
.gap { margin-top: var(--gap); }
.top { margin-bottom: var(--gap); }
/* koláč zdrojů návštěv: prstenec s mezerami mezi výsečemi, uprostřed součet, vedle legenda s čísly.
   Barvy: prvních pět slotů ověřené kategorické palety (validate_palette.js, i na spoji kruhu), zbytek šedé „Ostatní“. */
/* karta je stejně vysoká jako sousední „Nejčtenější stránky“; koláč zabere všechno místo nad legendou */
[data-slot="refs"], [data-slot="refs"] > .body { display: flex; flex-direction: column; }
[data-slot="refs"] > .body { flex: 1; }
.donut { flex: 1; display: flex; flex-direction: column; gap: 18px; }
/* koláč leží absolutně v rámečku .pie: jeho velikost určuje jen karta, samotný koláč rozložení neovlivňuje,
   takže se při najetí myší ani jiné změně stylu nepřepočítává a nemění velikost */
.donut .pie { position: relative; flex: 1 1 auto; min-height: 240px; }
.donut .pie svg { position: absolute; inset: 0; display: block; width: 100%; height: 100%; overflow: visible; }
/* zvýraznění jen průhledností: tvar se nemění, takže pod kurzorem nic neposkočí */
.donut circle.seg { fill: none; stroke-width: 26; transition: opacity .25s var(--ease); cursor: default; }
.donut.focus circle.seg { opacity: .3; }
.donut.focus circle.seg.on { opacity: 1; }
.grow .donut circle.seg { animation: sweep .9s var(--ease) backwards; animation-delay: calc(var(--i) * 70ms); }
@keyframes sweep { from { stroke-dasharray: 0 999; } }
.donut .n { font-size: 26px; font-weight: 600; letter-spacing: -.02em; fill: var(--ink); font-variant-numeric: tabular-nums; }
.donut .u { font-size: 11px; fill: #8e8e93; }
.legend { list-style: none; margin: 0; padding: 0; }
.legend li { position: relative; display: grid; grid-template-columns: 10px minmax(0, 1fr) auto; align-items: baseline; column-gap: 10px;
  padding: 8px 0; transition: opacity .25s var(--ease); }
/* rovné oddělovače, které se při najetí nemění */
.legend li + li::before { content: ""; position: absolute; left: 0; right: 0; top: 0; border-top: .5px solid #ececf0; }
/* najetí mění jen průhlednost ostatních řádků, nic jiného */
.donut.focus .legend li { opacity: .4; }
.donut.focus .legend li.on { opacity: 1; }
.legend i { width: 10px; height: 10px; border-radius: 3px; background: var(--c); align-self: center; }
.legend .k { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 14.5px; }
.legend .v { font-variant-numeric: tabular-nums; font-size: 14.5px; font-weight: 600; white-space: nowrap; }
.legend .v small { margin-left: 6px; font-size: 12px; font-weight: 500; color: #8e8e93; }
@media (max-width: 680px) { .donut .pie { flex: none; height: 240px; } }
/* souhrn karty Cookies: tři čísla ve stylu dlaždic webu */
.figs { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin-bottom: 14px; }
.fig { background: #f5f5f7; border-radius: 16px; padding: 11px 16px 12px; min-width: 0; }
.fig .lb { color: #8e8e93; }
.fig b { display: block; margin-top: 4px; font-size: 22px; font-weight: 600; letter-spacing: -.015em; font-variant-numeric: tabular-nums; line-height: 1.15; }
.fig .sx { margin-top: 3px; font-size: 12px; color: #6e6e73; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.fig .sx em { font-style: normal; color: var(--ink); font-weight: 600; }
.foot { margin: 10px 0 0; font-size: 12px; color: #8e8e93; }
.foot em { font-style: normal; color: var(--ink-2); font-weight: 600; }
@media (max-width: 680px) { .figs { grid-template-columns: minmax(0, 1fr); } }

/* skeleton jako na webu */
.sk { display: block; border-radius: 7px; background: linear-gradient(100deg, #ebebef 30%, #f6f6f8 45%, #ebebef 60%);
  background-size: 300% 100%; animation: sk 1.5s ease-in-out infinite; max-width: 100%; }
@keyframes sk { from { background-position: 100% 0; } to { background-position: 0 0; } }
.sk + .sk { margin-top: 10px; }
.sk-chart { height: 200px; display: flex; align-items: flex-end; gap: 6px; padding: 0 0 0 34px; }
.sk-chart .sk { flex: 1; margin: 0; border-radius: 4px 4px 0 0; }

/* sloupcový graf */
.chart { position: relative; height: 200px; margin: 6px 0 0 34px; }
.grid span { position: absolute; left: 0; right: 0; bottom: var(--y); border-top: 1px solid #f0f1f4; }
.grid span:first-child { border-top-color: var(--line); }
.grid i { position: absolute; right: 100%; transform: translate(-8px, -50%); font-size: 11px; font-style: normal; color: #8e8e93; font-variant-numeric: tabular-nums; }
.cols { position: absolute; inset: 0; display: flex; align-items: flex-end; gap: 2px; }
.col { flex: 1 1 0; height: 100%; display: flex; align-items: flex-end; outline: none; min-width: 0; }
.col span { display: block; width: 100%; height: var(--h); background: var(--accent); border-radius: 4px 4px 0 0;
  transform-origin: bottom; transition: opacity .2s; }
.grow .col span { animation: grow .7s var(--ease) backwards; animation-delay: calc(var(--i) * 12ms); }
@keyframes grow { from { transform: scaleY(0); } }
@media (hover: hover) { .cols:hover .col span { opacity: .4; } .cols .col:hover span { opacity: 1; } }
.col:focus-visible span, .col.on span { opacity: 1 !important; }
.axis { position: relative; height: 16px; margin: 7px 0 0 34px; }
.axis span { position: absolute; transform: translateX(-50%); font-size: 11px; color: #8e8e93; white-space: nowrap; }
.axis span:first-child { transform: none; }

/* tooltip grafu: vzhled jako u meteogramu (#tip v index.html), umístění uvnitř grafu jako .wtip v winters.js */
.ctip { position: absolute; top: 0; left: 0; z-index: 2; pointer-events: none; min-width: 150px; padding: 9px 12px 8px; font-size: 12.5px; white-space: nowrap;
  background: rgba(255,255,255,.8); -webkit-backdrop-filter: saturate(180%) blur(20px); backdrop-filter: saturate(180%) blur(20px);
  border: .5px solid rgba(0,0,0,.1); border-radius: 14px; box-shadow: 0 12px 32px rgba(0,0,0,.12), 0 1px 3px rgba(0,0,0,.08); }
.ctip .t { color: #6e6e73; font-weight: 600; padding-bottom: 4px; }
.ctip .r { display: flex; justify-content: space-between; gap: 16px; font-variant-numeric: tabular-nums; }
.ctip .r b { font-weight: 600; }

/* rozpisy s pruhem */
/* rozpisy: název a hodnota nahoře, pod nimi samostatný tenký pruh (jako Čas u obrazovky v iOS a .st .bar na webu) */
.rows { list-style: none; margin: 0; padding: 0; }
.rows li { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: baseline; column-gap: 12px; padding: 8px 0 9px; }
.rows li + li, .rows + .rows > li:first-child { border-top: .5px solid #ececf0; }
.rows .k { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 14.5px; letter-spacing: -.005em; }
.rows .v { font-variant-numeric: tabular-nums; font-size: 14.5px; font-weight: 600; white-space: nowrap; }
.rows .k small { margin-left: 8px; font-size: 12.5px; color: #8e8e93; }
.rows .v small { margin-left: 6px; font-size: 12px; font-weight: 500; color: #8e8e93; }
.rows .track { grid-column: 1 / -1; height: 5px; margin-top: 7px; border-radius: 3px; background: #ededf1; overflow: hidden; }
.rows .track i { display: block; height: 100%; width: var(--w); border-radius: 3px; background: var(--accent); transform-origin: left;
  opacity: calc(1 - var(--i) * .06); transition: opacity .15s; }
.grow .rows .track i { animation: bar .8s var(--ease) backwards; animation-delay: calc(var(--i) * 40ms); }
@keyframes bar { from { transform: scaleX(0); } }
@media (hover: hover) { .rows li:hover .track i { opacity: 1; } }
.path { color: #8e8e93; font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 13px; }
.more { display: inline-flex; align-items: center; gap: 4px; margin-top: 8px; padding: 6px 10px; margin-left: -10px; border-radius: 9px;
  background: none; border: 0; font: inherit; font-size: 13px; font-weight: 600; color: var(--accent); cursor: pointer; transition: background .15s, transform .15s var(--ease); }
.more:hover { background: var(--accent-bg); }
.more:active { transform: scale(.96); }
.empty { color: #8e8e93; margin: 2px 0; font-size: 14px; }

/* stav webu */
.status { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 4px 24px; }
.status li { display: flex; gap: 10px; align-items: flex-start; padding: 7px 0; }
.status .ic { flex: 0 0 20px; height: 20px; border-radius: 50%; display: grid; place-items: center; color: #fff; margin-top: 1px; }
.status .ok .ic { background: var(--good); }
.status .bad .ic { background: var(--warn); }
.status b { display: block; font-weight: 600; font-size: 14px; }
.status small { display: block; color: #8e8e93; font-size: 12px; }
.status .bad small { color: var(--warn); }
.pill { display: inline-flex; align-items: center; font-size: 11px; font-weight: 600; padding: 3px 8px; border-radius: 99px; letter-spacing: 0; text-transform: none; margin-left: 4px; }
.pill.ok { background: #e6f4e8; color: #22762f; }
.pill.bad { background: #fcebdd; color: #9c4409; }

footer { margin-top: 28px; display: flex; gap: 8px 16px; flex-wrap: wrap; align-items: center; justify-content: space-between; color: #8e8e93; font-size: 12px; }
footer form { display: inline; }

button { font: inherit; border: 0; cursor: pointer; }
/* stejné jako „Otevřít meteogram“ na kartách úvodní stránky (.hc-f v index.html) */
.primary { display: flex; justify-content: center; align-items: center; gap: 6px; width: 100%; height: 40px; padding: 0 16px; border-radius: 12px;
  background: var(--accent-bg); color: var(--accent); font-size: 14px; font-weight: 600; transition: background .15s, color .15s, transform .15s var(--ease); }
.primary svg { transition: transform .15s; }
.primary:focus-visible { outline: none; box-shadow: 0 0 0 4px var(--accent-bg), 0 0 0 5px var(--accent); }
@media (hover: hover) { .primary:hover { background: var(--accent); color: #fff; } .primary:hover svg { transform: translateX(2px); } }
.primary:active { transform: scale(.98); transition-duration: .1s; }
.ghost { background: transparent; color: var(--accent); padding: 6px 10px; border-radius: 9px; font-weight: 600; font-size: 13px; transition: background .15s, transform .15s var(--ease); }
.ghost:hover { background: var(--accent-bg); }
.ghost:active { transform: scale(.96); }

/* přihlášení */
.login { min-height: 100vh; min-height: 100dvh; display: grid; place-items: center; padding: 20px; }
.login .card { width: 100%; max-width: 380px; padding: 30px 28px 26px; text-align: center; animation: rise .5s var(--ease) backwards; }
.login .gl { width: 64px; margin: 0 auto; box-shadow: 0 1px 2px rgba(16,60,120,.16), 0 6px 18px rgba(16,60,120,.16); }
.login h1 { font-size: 22px; letter-spacing: -.02em; margin: 16px 0 4px; }
.login p { color: #6e6e73; margin: 0 0 20px; }
.login .msg { color: var(--warn); font-size: 14px; margin: -6px 0 16px; animation: shake .4s var(--ease); }
.login .note { color: #8e8e93; font-size: 13px; margin: 16px 0 0; }
.code { width: 100%; font: 600 28px/1 ui-monospace, "SF Mono", Menlo, monospace; letter-spacing: .35em; text-align: center; padding: 14px 10px 14px calc(10px + .35em);
  border: 1px solid var(--line); border-radius: 12px; margin-bottom: 12px; background: var(--soft); color: var(--ink); transition: border-color .15s, background .15s, box-shadow .15s; }
.code:focus { outline: none; border-color: var(--accent); background: var(--paper); box-shadow: 0 0 0 4px var(--accent-bg); }
@keyframes rise { from { opacity: 0; transform: translateY(14px) scale(.98); } }
@keyframes shake { 20%, 60% { transform: translateX(-4px); } 40%, 80% { transform: translateX(4px); } }

@media (max-width: 1020px) { .hl { grid-template-columns: repeat(2, minmax(0, 1fr)); } .g3, .status { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 680px) {
  :root { --gap: 12px; }
  .wrap { padding-left: max(16px, env(safe-area-inset-left)); padding-right: max(16px, env(safe-area-inset-right)); }
  .g2, .g3, .status { grid-template-columns: minmax(0, 1fr); }
  .seg { width: 100%; } .seg a { flex: 1; padding: 6px 4px; }
  .chart, .sk-chart { height: 160px; }
  .card { padding: 16px 16px 14px; border-radius: 20px; }
}
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation: none !important; transition-duration: .01ms !important; }
  .st:hover { transform: none; }
}
</style>
</head>
<body>
<?php if (!$logged): ?>
<main class="login">
  <div class="card">
    <span class="gl"><img src="/assets/logo.png?v=4" width="64" height="64" alt=""></span>
    <h1>Správa webu</h1>
    <?php if ($step === 'kod'): ?>
      <p>Kód jsme poslali na <?= e($mail ? masked($mail) : 'váš e-mail') ?>. Platí 10 minut.</p>
      <?php if ($error): ?><p class="msg" role="alert"><?= e($error) ?></p><?php endif ?>
      <form method="post">
        <input type="hidden" name="akce" value="overit">
        <input class="code" name="kod" inputmode="numeric" autocomplete="one-time-code" pattern="[0-9 ]{6,7}" maxlength="7" required autofocus aria-label="Kód z e-mailu" placeholder="••••••">
        <button class="primary">Přihlásit<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6"/></svg></button>
      </form>
      <?php if ($DEV): ?><form method="post"><input type="hidden" name="akce" value="lokalne"><p class="note"><button class="ghost">Přihlásit bez kódu (jen lokální test)</button></p></form><?php endif ?>
      <form method="post"><input type="hidden" name="akce" value="poslat"><p class="note">Nepřišel kód? <button class="ghost">Poslat znovu</button></p></form>
    <?php else: ?>
      <p><?= isset($_GET['odhlaseno']) ? 'Jste odhlášeni.' : 'Meteogramy.cz' ?></p>
      <?php if ($error): ?><p class="msg" role="alert"><?= e($error) ?></p><?php endif ?>
      <form method="post">
        <input type="hidden" name="akce" value="poslat">
        <button class="primary">Poslat přihlašovací kód<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6"/></svg></button>
      </form>
      <p class="note">Kód přijde e-mailem na adresu správce webu.</p>
      <?php if ($DEV): ?><form method="post"><input type="hidden" name="akce" value="lokalne"><p class="note"><button class="ghost">Přihlásit bez kódu (jen lokální test)</button></p></form><?php endif ?>
    <?php endif ?>
  </div>
</main>
<?php else: ?>
<div class="wrap">
  <header>
    <a class="brand" href="/" title="Otevřít web"><span class="gl"><img src="/assets/logo.png?v=4" width="40" height="40" alt=""></span><span><b>Správa webu</b><small>Meteogramy.cz</small></span></a>
    <nav class="seg" id="seg" aria-label="Období"><span class="thumb" aria-hidden="true"></span>
      <?php foreach (RANGES as $k => $lab): ?><a href="?dny=<?= $k ?>" data-span="<?= $k ?>"<?= $k === $span ? ' aria-current="true"' : '' ?>><?= $lab ?></a><?php endforeach ?>
    </nav>
  </header>

  <section class="card top" data-slot="status"><h2 class="lb"><?= icon('pulse') ?><span>Stav webu</span><span class="pill" id="pill" hidden></span></h2><div class="body"></div></section>

  <section class="hl" aria-live="polite">
    <?= tile('uv', 'user', 'Návštěvy') ?>
    <?= tile('pv', 'eye', 'Zobrazení stránek') ?>
    <?= tile('today', 'clock', 'Dnes') ?>
    <?= tile('mobile', 'phone', 'Z mobilu a tabletu') ?>
  </section>

  <section class="card" data-slot="daily"><?= head('chart', 'Návštěvy po dnech') ?><div class="body"></div></section>
  <section class="card gap" data-slot="hours"><?= head('clock', 'Během dne', '· zobrazení podle hodiny') ?><div class="body"></div></section>
  <div class="g2">
    <section class="card" data-slot="pages"><?= head('page', 'Nejčtenější stránky') ?><div class="body"></div></section>
    <section class="card" data-slot="refs"><?= head('link', 'Odkud přicházejí') ?><div class="body"></div></section>
  </div>
  <div class="g3">
    <section class="card" data-slot="dev"><?= head('phone', 'Zařízení') ?><div class="body"></div></section>
    <section class="card" data-slot="br"><?= head('globe', 'Prohlížeče') ?><div class="body"></div></section>
    <section class="card" data-slot="os"><?= head('screen', 'Systémy') ?><div class="body"></div></section>
  </div>
  <section class="card gap" data-slot="consent"><?= head('cookie', 'Cookies', '· jak lidé odpovídají na lištu se souhlasem') ?><div class="body"></div></section>
  <section class="card gap" data-slot="fav"><?= head('star', 'Oblíbené stanice', '· otevření za 30 dní') ?><div class="body"></div></section>

  <footer>
    <span>Anonymní součty bez cookies · návštěva = jeden návštěvník za den</span>
    <span>
      <form method="post"><input type="hidden" name="akce" value="odhlasit"><button class="ghost">Odhlásit</button></form>
      <form method="post"><input type="hidden" name="akce" value="odhlasit-vse"><button class="ghost">Odhlásit všechna zařízení</button></form>
    </span>
  </footer>
</div>
<script nonce="<?= $NONCE ?>">
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const calm = matchMedia("(prefers-reduced-motion: reduce)").matches;
const esc = s => String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const num = v => Math.round(v).toLocaleString("cs-CZ");
const pct = (part, all) => all ? Math.round(100 * part / all) + " %" : "–";
const WD = ["ne", "po", "út", "st", "čt", "pá", "so"];
const day = (iso, wd = true) => { const d = new Date(iso + "T12:00:00"); return (wd ? WD[d.getDay()] + " " : "") + `${d.getDate()}. ${d.getMonth() + 1}.`; };
const ICON_OK = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.4" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>';
const ICON_BAD = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.4" stroke-linecap="round"><path d="M12 6v7M12 18h.01"/></svg>';
let span = <?= $span ?>, req = 0;

// --- skeletony a objevení obsahu (jako reveal() na webu) ---
function skeleton() {
  $$("[data-tile]").forEach(t => {
    t.querySelector("b").innerHTML = '<i class="sk" style="width:46%;height:22px"></i>';
    t.querySelector(".sx").innerHTML = '<i class="sk" style="width:72%;height:10px"></i>';
  });
  const bars = n => `<div class="sk-chart">${Array.from({ length: n }, (_, i) => `<i class="sk" style="height:${25 + 55 * Math.abs(Math.sin(i * 1.7))}%"></i>`).join("")}</div>`;
  const lines = n => Array.from({ length: n }, (_, i) => `<i class="sk" style="width:${92 - i * 14}%;height:16px"></i>`).join("");
  $$("[data-slot]").forEach(s => {
    const k = s.dataset.slot;
    s.querySelector(".body").innerHTML = k === "daily" ? bars(16) : k === "hours" ? bars(12) : k === "status" ? lines(3) : lines(4);
  });
  $("#pill").hidden = true;
}
function reveal() {
  if (calm) return;
  [...$$(".st"), ...$$(".card")].forEach((el, k) => el.animate(
    [{ opacity: 0, transform: "translateY(8px)" }, { opacity: 1, transform: "none" }],
    { duration: 380, delay: k * 40, easing: "cubic-bezier(.2, .8, .2, 1)", fill: "backwards" }));
}
// čísla v dlaždicích naskočí od nuly
function countUp(el, to, fmt) {
  if (calm || !to) { el.textContent = fmt(to); return; }
  const t0 = performance.now(), dur = 700;
  const step = t => { const p = Math.min(1, (t - t0) / dur), e = 1 - Math.pow(1 - p, 3); el.textContent = fmt(to * e); if (p < 1) requestAnimationFrame(step); };
  requestAnimationFrame(step);
}

// --- vykreslení ---
function delta(now, before) {
  if (!before) return "";
  const d = Math.round(100 * (now - before) / before);
  return `<em>${d > 0 ? "↑ +" : d < 0 ? "↓ −" : "± "}${Math.abs(d)} %</em> proti předchozím ${span === 365 ? "365 dnům" : span + " dnům"}`;
}
function tile(slot, value, fmt, sub) {
  const t = $(`[data-tile="${slot}"]`);
  const b = t.querySelector("b");
  countUp(b, value, fmt);
  t.querySelector(".sx").innerHTML = sub;
}
function rows(list, { limit = 8, percent = false, path = false } = {}) {
  if (!list.length) return '<p class="empty">Zatím žádná data.</p>';
  const total = list.reduce((a, x) => a + x[1], 0), max = list[0][1];
  // hlavní hodnota tučně, druhá (podíl nebo počet) drobně šedě; pruh je relativní k největší položce
  const li = ([k, v, sub], i) => `<li><span class="k">${path && k.startsWith("/") ? `<span class="path">${esc(k)}</span>` : esc(k)}${sub ? `<small>${esc(sub)}</small>` : ""}</span>` +
    `<span class="v">${percent ? pct(v, total) : num(v)}<small>${percent ? num(v) : pct(v, total)}</small></span>` +
    `<span class="track"><i style="--w:${Math.max(1.5, 100 * v / max).toFixed(1)}%;--i:${Math.min(i, 8)}"></i></span></li>`;
  const head = list.slice(0, limit).map(li).join(""), rest = list.slice(limit).map((x, i) => li(x, limit + i)).join("");
  return `<ul class="rows">${head}</ul>` + (rest ? `<ul class="rows" hidden>${rest}</ul><button class="more" type="button">Zobrazit všech ${list.length}</button>` : "");
}
// prstencový koláč s legendou; list = [[název, počet], ...] seřazený od největšího
const SLICES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"], OTHER = "#aeaeb2";
function donut(list, unit) {
  if (!list.length) return '<p class="empty">Zatím žádná data.</p>';
  let parts = list.filter(x => x[0] !== "Ostatní");
  const rest = list.filter(x => x[0] === "Ostatní").reduce((a, x) => a + x[1], 0) + parts.slice(SLICES.length).reduce((a, x) => a + x[1], 0);
  parts = parts.slice(0, SLICES.length).map((x, i) => [...x, SLICES[i]]);
  if (rest) parts.push(["Ostatní", rest, OTHER]);
  const total = parts.reduce((a, x) => a + x[1], 0);
  const R = 70, C = 2 * Math.PI * R, gap = parts.length > 1 ? 2.5 : 0;
  let at = 0;
  const segs = parts.map(([k, v, c], i) => {
    const len = Math.max(0, v / total * C - gap);
    const seg = `<circle class="seg" data-i="${i}" r="${R}" cx="100" cy="100" stroke="${c}" stroke-dasharray="${len.toFixed(2)} ${(C - len).toFixed(2)}" ` +
      `stroke-dashoffset="${(-at).toFixed(2)}" style="--i:${i}"><title>${esc(k)}: ${num(v)} (${pct(v, total)})</title></circle>`;
    at += v / total * C;
    return seg;
  }).join("");
  const legend = parts.map(([k, v, c], i) => `<li data-i="${i}"><i style="--c:${c}"></i><span class="k">${esc(k)}</span>` +
    `<span class="v">${num(v)}<small>${pct(v, total)}</small></span></li>`).join("");
  return `<div class="donut"><div class="pie"><svg viewBox="0 0 200 200" role="img" aria-label="${esc(unit)}"><g transform="rotate(-90 100 100)">${segs}</g>` +
    `<text x="100" y="100" text-anchor="middle"><tspan class="n" x="100" dy="2">${num(total)}</tspan><tspan class="u" x="100" dy="18">${esc(unit)}</tspan></text></svg></div>` +
    `<ul class="legend">${legend}</ul></div>`;
}
// Najetí na výseč nebo řádek legendy zvýrazní obojí. Mezery mezi výsečemi a řádky zvýraznění neruší,
// zhasne až po opuštění celého koláče s legendou.
function highlight(d, i) {
  d.classList.toggle("focus", i !== null);
  d.querySelectorAll("[data-i]").forEach(x => x.classList.toggle("on", x.dataset.i === i));
}
document.addEventListener("pointerover", e => {
  const t = e.target.closest(".donut [data-i]");
  if (t) highlight(t.closest(".donut"), t.dataset.i);
});
document.addEventListener("pointerout", e => {
  const d = e.target.closest(".donut");
  if (d && !d.contains(e.relatedTarget)) highlight(d, null);
});

// sloupcový graf; items = [{ axis, v, title, lines: [[popisek, hodnota], ...] }]
function columns(items, every) {
  const max = Math.max(1, ...items.map(x => x.v));
  const tick = [1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 2500, 5000, 10000, 20000, 50000].find(s => max / s <= 4) || 100000;
  const top = Math.max(tick, Math.ceil(max / tick) * tick);
  let grid = "";
  for (let v = 0; v <= top; v += tick) grid += `<span style="--y:${(100 * v / top).toFixed(2)}%"><i>${num(v)}</i></span>`;
  const cols = items.map((x, i) => `<div class="col" tabindex="0" data-i="${i}"><span style="--h:${x.v ? Math.max(1, 100 * x.v / top).toFixed(2) : 0}%;--i:${i}"></span></div>`).join("");
  const axis = items.map((x, i) => i % every ? "" : `<span style="left:${(100 * (i + .5) / items.length).toFixed(2)}%">${esc(x.axis)}</span>`).join("");
  return { html: `<div class="chart"><div class="grid">${grid}</div><div class="cols">${cols}</div><div class="ctip" role="tooltip" hidden></div></div><div class="axis">${axis}</div>`, items };
}
function mountChart(slot, chart) {
  const body = $(`[data-slot="${slot}"] .body`);
  body.innerHTML = chart.html;
  hover(body.querySelector(".chart"), chart.items);
}
// Stejně jako hover() v assets/winters.js: bublina stojí vpravo od sloupce i kurzoru, a když se v grafu
// nevejde, vlevo od nich; svisle u kurzoru. Nikdy nevyjede z grafu. Dotyk: vodorovný tah prochází sloupce,
// svislý tah bublinu skryje, aby šla stránka posouvat.
const charts = [];
function hover(box, items) {
  const cols = box.querySelector(".cols"), tip = box.querySelector(".ctip"), bars = [...cols.children];
  let on = null;
  const hide = () => { tip.hidden = true; if (on) on.classList.remove("on"); on = null; };
  const show = (i, clientX, clientY) => {
    const x = items[i], el = bars[i];
    if (on !== el) {
      if (on) on.classList.remove("on");
      on = el; el.classList.add("on");
      tip.innerHTML = `<div class="t">${esc(x.title)}</div>` + x.lines.map(([k, v]) => `<div class="r"><span>${esc(k)}</span><b>${esc(v)}</b></div>`).join("");
    }
    tip.hidden = false;
    const r = box.getBoundingClientRect(), b = el.getBoundingClientRect();
    const px = b.left - r.left + b.width / 2;
    const pointerX = clientX == null ? px : clientX - r.left;
    const pointerY = clientY == null ? el.firstChild.getBoundingClientRect().top - r.top : clientY - r.top;
    const right = Math.max(px, pointerX) + 14;
    const left = Math.min(px, pointerX) - tip.offsetWidth - 14;
    const maxLeft = r.width - tip.offsetWidth - 8;
    tip.style.left = Math.max(8, Math.min(right <= maxLeft ? right : left, maxLeft)) + "px";
    tip.style.top = Math.max(0, Math.min(r.height - tip.offsetHeight, pointerY - tip.offsetHeight / 2)) + "px";
  };
  const move = e => {
    const r = cols.getBoundingClientRect();
    const i = Math.floor((e.clientX - r.left) / r.width * items.length);
    if (i < 0 || i >= items.length) { hide(); return; }
    show(i, e.clientX, e.clientY);
  };
  let touch = null, direction = null;
  cols.addEventListener("pointerdown", e => {
    if (e.pointerType === "mouse") return;
    touch = { x: e.clientX, y: e.clientY }; direction = null;
    move(e);
  });
  cols.addEventListener("pointermove", e => {
    if (e.pointerType === "mouse") { move(e); return; }
    if (!touch) return;
    const dx = Math.abs(e.clientX - touch.x), dy = Math.abs(e.clientY - touch.y);
    if (!direction && Math.max(dx, dy) > 8) direction = dx > dy ? "horizontal" : "vertical";
    if (direction === "vertical") hide();
    else move(e);
  });
  cols.addEventListener("pointerup", () => { touch = null; });
  cols.addEventListener("pointercancel", () => { touch = null; hide(); });
  cols.addEventListener("pointerleave", e => { if (e.pointerType === "mouse") hide(); });
  bars.forEach((el, i) => { el.addEventListener("focus", () => show(i)); el.addEventListener("blur", hide); });
  box.hideTip = hide;
  charts.push(box);
}
function hideTip() { charts.forEach(b => b.isConnected && b.hideTip()); }
document.addEventListener("click", e => { charts.forEach(b => { if (b.isConnected && !b.contains(e.target)) b.hideTip(); }); });

function ago(ts, now) {
  if (!ts) return "nikdy";
  const s = now - ts, d = new Date(ts * 1000);
  const when = `${d.getDate()}. ${d.getMonth() + 1}. ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  return when + " · " + (s < 90 ? "před chvílí" : s < 5400 ? `před ${Math.round(s / 60)} min` : s < 172800 ? `před ${Math.round(s / 3600)} h` : `před ${Math.round(s / 86400)} dny`);
}

function render(d) {
  const perVisit = d.uv ? (d.pv / d.uv).toLocaleString("cs-CZ", { maximumFractionDigits: 1 }) : "–";
  tile("uv", d.uv, num, d.prev.uv ? delta(d.uv, d.prev.uv) : `za ${span === 365 ? "poslední rok" : `posledních ${span} dní`}`);
  tile("pv", d.pv, num, `${d.prev.pv ? delta(d.pv, d.prev.pv).replace(/ proti.*/, "") + " · " : ""}<em>${perVisit}</em> na návštěvu`);
  tile("today", d.today.uv, num, `<em>${num(d.today.pv)}</em> zobrazení`);
  tile("mobile", d.uv ? 100 * d.mobile / d.uv : 0, v => d.uv ? Math.round(v) + " %" : "–", `<em>${num(d.app)}×</em> z ikony na ploše`);

  charts.length = 0;
  const every = span <= 7 ? 1 : span <= 30 ? 5 : span <= 90 ? 14 : 61;
  mountChart("daily", columns(d.daily.map(([iso, uv, pv]) => ({ axis: day(iso, span <= 7), v: uv, title: day(iso),
    lines: [["Návštěvy", num(uv)], ["Zobrazení", num(pv)]] })), every));
  mountChart("hours", columns(d.hours.map((v, h) => ({ axis: String(h), v, title: `${h}:00–${h + 1}:00`, lines: [["Zobrazení", num(v)]] })), 6));
  $('[data-slot="pages"] .body').innerHTML = rows(d.pages, { limit: 10, path: true });
  $('[data-slot="refs"] .body').innerHTML = donut(d.refs, "návštěv");
  for (const k of ["dev", "br", "os"]) $(`[data-slot="${k}"] .body`).innerHTML = rows(d[k], { percent: true });
  $('[data-slot="fav"] .body').innerHTML = rows(d.fav, { limit: 10 });

  // cookies: zobrazení lišty a rozhodnutí v ní
  const c = d.consent, cv = k => c[k] || 0;
  const yes = cv("all") + cv("custom-yes"), no = cv("no") + cv("custom-no"), decided = yes + no;
  const outcomes = [["Přijmout vše", cv("all")], ["Odmítnout", cv("no")], ["Vlastní výběr s analytikou", cv("custom-yes")],
    ["Vlastní výběr bez analytiky", cv("custom-no")], ["Zavřeno křížkem bez volby", cv("closed")]].filter(x => x[1]).sort((a, b) => b[1] - a[1]);
  const fig = (label, value, sub) => `<div class="fig"><div class="lb"><span>${label}</span></div><b>${value}</b><div class="sx">${sub}</div></div>`;
  $('[data-slot="consent"] .body').innerHTML = !cv("shown") && !outcomes.length ? '<p class="empty">Zatím žádná data.</p>' :
    `<div class="figs">${fig("Lišta se ukázala", num(cv("shown")), "nových návštěvníků bez uložené volby")}` +
    fig("Souhlas s analytikou", pct(yes, decided), `<em>${num(yes)}</em> z ${num(decided)} rozhodnutí`) +
    fig("Rozhodlo se", pct(Math.min(decided, cv("shown")), cv("shown")), `<em>${num(cv("closed"))}</em> zavřelo bez volby`) + `</div>` +
    rows(outcomes, { limit: 8 }) +
    `<p class="foot">Nastavení z lišty otevřeno <em>${num(cv("settings"))}×</em> · znovu přes odkaz Cookies <em>${num(cv("reopen"))}×</em></p>`;

  const bad = d.status.filter(s => !s.fine).length;
  const pill = $("#pill");
  pill.className = "pill " + (bad ? "bad" : "ok");
  pill.textContent = bad ? (bad === 1 ? "1 problém" : bad < 5 ? `${bad} problémy` : `${bad} problémů`) : "vše v pořádku";
  pill.hidden = false;
  $('[data-slot="status"] .body').innerHTML = `<ul class="status">${d.status.map(s => `<li class="${s.fine ? "ok" : "bad"}"><span class="ic" aria-hidden="true">${s.fine ? ICON_OK : ICON_BAD}</span>` +
    `<span><b>${esc(s.name)}</b><small>${s.fine ? "" : "Pozor: "}${esc(s.desc)} · ${esc(ago(s.ts, d.now))}</small></span></li>`).join("")}</ul>`;

  document.body.classList.remove("grow");
  void document.body.offsetWidth;
  document.body.classList.add("grow");
}

async function load(first) {
  const id = ++req;
  // krátké načtení skeletonem neblikne; ten se ukáže jen při prvním otevření nebo když data chvíli trvají
  const timer = first ? (skeleton(), 0) : setTimeout(skeleton, 150);
  try {
    const r = await fetch(`?data=1&dny=${span}`, { credentials: "same-origin", cache: "no-store" });
    if (r.status === 401) return location.reload();
    const d = await r.json();
    if (id !== req) return;
    clearTimeout(timer);
    render(d);
    reveal();
  } catch (e) {
    console.error(e);
    if (id !== req) return;
    clearTimeout(timer);
    $$("[data-slot] .body").forEach(b => b.innerHTML = '<p class="empty">Data se nepodařilo načíst. Zkuste obnovit stránku.</p>');
  }
}

// přepínač období s posuvným jezdcem
const seg = $("#seg"), thumb = seg.querySelector(".thumb");
function moveThumb(animate) {
  const a = seg.querySelector("[aria-current]");
  if (!animate) thumb.style.transition = "none";
  thumb.style.width = a.offsetWidth + "px";
  thumb.style.transform = `translateX(${a.offsetLeft}px)`;
  if (!animate) { void thumb.offsetWidth; thumb.style.transition = ""; }
}
seg.addEventListener("click", e => {
  const a = e.target.closest("a[data-span]");
  if (!a || e.metaKey || e.ctrlKey) return;
  e.preventDefault();
  if (+a.dataset.span === span) return;
  seg.querySelectorAll("a").forEach(x => x.removeAttribute("aria-current"));
  a.setAttribute("aria-current", "true");
  moveThumb(true);
  span = +a.dataset.span;
  history.replaceState(null, "", `?dny=${span}`);
  hideTip();
  load(false);
});
addEventListener("resize", () => moveThumb(false));
document.addEventListener("click", e => {
  const b = e.target.closest(".more");
  if (!b) return;
  b.previousElementSibling.hidden = false;
  b.remove();
});
moveThumb(false);
load(true);
</script>
<?php endif ?>
</body>
</html>
