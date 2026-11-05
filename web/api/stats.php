<?php
// Anonymní návštěvnost bez cookies (POST z index.html při zobrazení stránky).
// Ukládá jen denní součty: zobrazení po stránkách a hodinách, zdroj (jen doména), typ zařízení,
// prohlížeč a systém. Unikátní návštěvníky počítá podle otisku z IP adresy, prohlížeče a soli,
// která platí jediný den a pak se zahodí; IP adresa ani otisk se do součtů neukládají.
// Přehled zobrazuje /sprava-webu-meteo/. Soubory leží v api/stats/, kam není přímý přístup.

const KEEP_DAYS = 400;   // jak dlouho držet denní součty
const MAX_KEYS = 300;    // strop klíčů v jedné kategorii za den (ochrana proti zahlcení)
$dir = __DIR__ . '/stats';

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');
header('X-Robots-Tag: noindex');

function done($code = 204) {
    http_response_code($code);
    exit;
}

if ($_SERVER['REQUEST_METHOD'] !== 'POST') done(405);

$ua = substr((string)($_SERVER['HTTP_USER_AGENT'] ?? ''), 0, 400);
if ($ua === '' || preg_match('/bot|crawl|spider|slurp|preview|headless|lighthouse|pingdom|uptime|monitor|curl|wget|python|scrapy|httpclient|java\//i', $ua)) done();

date_default_timezone_set('Europe/Prague');

// Rozhodnutí v liště cookies: jen denní počet každé volby, bez vazby na návštěvníka ani stránku.
const CONSENT = ['shown', 'all', 'no', 'custom-yes', 'custom-no', 'closed', 'settings', 'reopen'];
if (isset($_POST['e'])) {
    $event = (string)$_POST['e'];
    if (!in_array($event, CONSENT, true)) done(400);
    if (!is_dir("$dir/days")) mkdir("$dir/days", 0755, true);
    update("$dir/days/" . date('Y-m-d') . '.json', function (&$d) use ($event) {
        if (!isset($d['consent']) || !is_array($d['consent'])) $d['consent'] = [];
        $d['consent'][$event] = ($d['consent'][$event] ?? 0) + 1;
    });
    done();
}

// jen existující stránky webu: úvod nebo vygenerovaná složka s index.html
$path = (string)($_POST['p'] ?? '');
if (!preg_match('~^/([a-z0-9-]{1,80}/)?$~', $path)) done(400);
if ($path !== '/' && !is_file(dirname(__DIR__) . $path . 'index.html')) done(400);

function source($ref) {
    $host = strtolower((string)parse_url($ref, PHP_URL_HOST));
    $host = preg_replace('/^(www|m|l|lm|mobile)\./', '', $host);
    if ($host === '' || $host === 'meteogramy.cz' || !preg_match('/^[a-z0-9.-]{1,100}$/', $host)) return '';
    if (preg_match('/(^|\.)google\.[a-z.]+$/', $host)) return 'google';
    if (preg_match('/(^|\.)seznam\.cz$/', $host)) return 'seznam.cz';
    if (preg_match('/(^|\.)bing\.com$/', $host)) return 'bing.com';
    if (preg_match('/(^|\.)(facebook\.com|fb\.me)$/', $host)) return 'facebook.com';
    if ($host === 't.co' || $host === 'twitter.com') return 'x.com';
    return $host;
}

function device($ua) {
    if (preg_match('/iPad|Tablet|Android(?!.*Mobile)/i', $ua)) return 'tablet';
    if (preg_match('/Mobi|iPhone|iPod|Android/i', $ua)) return 'mobil';
    return 'počítač';
}

function browser($ua) {
    foreach (['Edge' => 'Edg', 'Opera' => 'OPR|Opera', 'Samsung' => 'SamsungBrowser', 'Firefox' => 'Firefox|FxiOS',
              'Chrome' => 'Chrome|CriOS', 'Safari' => 'Safari'] as $name => $re) {
        if (preg_match("~$re~", $ua)) return $name;
    }
    return 'jiný';
}

function system_of($ua) {
    foreach (['iOS' => 'iPhone|iPad|iPod', 'Android' => 'Android', 'macOS' => 'Macintosh', 'Windows' => 'Windows',
              'Linux' => 'Linux|X11'] as $name => $re) {
        if (preg_match("~$re~", $ua)) return $name;
    }
    return 'jiný';
}

function add(&$map, $key) {
    if (!is_array($map)) $map = [];
    if (!isset($map[$key]) && count($map) >= MAX_KEYS) $key = 'ostatní';
    $map[$key] = ($map[$key] ?? 0) + 1;
}

// Otevře JSON soubor se zámkem; $fn upraví data, výsledek se zapíše zpět.
function update($file, $fn) {
    $fp = fopen($file, 'c+');
    if (!$fp || !flock($fp, LOCK_EX)) return null;
    $data = json_decode(stream_get_contents($fp), true);
    if (!is_array($data)) $data = [];
    $result = $fn($data);
    ftruncate($fp, 0);
    rewind($fp);
    fwrite($fp, json_encode($data, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES));
    fflush($fp);
    flock($fp, LOCK_UN);
    fclose($fp);
    return $result;
}

$day = date('Y-m-d');
if (!is_dir("$dir/days")) mkdir("$dir/days", 0755, true);
// druhá pojistka k pravidlu v kořenovém .htaccess: obsah složky se nikdy neservíruje
if (!is_file("$dir/.htaccess")) file_put_contents("$dir/.htaccess",
    "<IfModule mod_authz_core.c>\n  Require all denied\n</IfModule>\n<IfModule !mod_authz_core.c>\n  Deny from all\n</IfModule>\n");

// Denní sůl a otisky dnešních návštěvníků. Se změnou dne se obojí zahodí.
$ip = (string)($_SERVER['REMOTE_ADDR'] ?? '');
$cleanup = false;
$new = update("$dir/visitors.json", function (&$v) use ($day, $ip, $ua, &$cleanup) {
    if (($v['day'] ?? '') !== $day) {
        $v = ['day' => $day, 'salt' => bin2hex(random_bytes(16)), 'seen' => []];
        $cleanup = true;
    }
    $id = substr(hash('sha256', $v['salt'] . "\n" . $ip . "\n" . $ua), 0, 16);
    if (isset($v['seen'][$id])) return false;
    $v['seen'][$id] = 1;
    return true;
});

$ref = (string)($_POST['r'] ?? '');
update("$dir/days/$day.json", function (&$d) use ($new, $path, $ref, $ua) {
    $d['pv'] = ($d['pv'] ?? 0) + 1;
    $hour = (int)date('G');
    $d['hours'] = $d['hours'] ?? array_fill(0, 24, 0);
    $d['hours'][$hour]++;
    add($d['pages'], $path);
    if ($new) {
        // zdroj, zařízení a prohlížeč se počítají jednou za návštěvníka a den
        $d['uv'] = ($d['uv'] ?? 0) + 1;
        add($d['refs'], source($ref) ?: '(přímo)');
        add($d['dev'], device($ua));
        add($d['br'], browser($ua));
        add($d['os'], system_of($ua));
        if (($_POST['a'] ?? '') === '1') $d['app'] = ($d['app'] ?? 0) + 1;
    }
});

if ($cleanup) {
    $cutoff = date('Y-m-d', time() - KEEP_DAYS * 86400);
    foreach (glob("$dir/days/*.json") as $f) if (basename($f, '.json') < $cutoff) unlink($f);
}
done();
