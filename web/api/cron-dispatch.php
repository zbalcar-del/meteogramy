<?php
// Hodinové spuštění aktualizace dat. Volá ho plánovač úloh (cron) hostingu Webglobe,
// protože plánovač GitHub Actions běhy často vynechává.
// Skript jen pošle GitHubu repository_dispatch; samotná aktualizace běží v GitHub Actions.
//
// Token (fine-grained, jen repo meteogramy, Contents: Read and write) leží MIMO public_html:
//   /github-dispatch-token.txt   (o úroveň výš než public_html, nahrává se ručně, ne z gitu)
// Volat ho může kdokoli, proto spuštění omezuje MIN_GAP; častější volání nic nespustí.

const REPO = 'zbalcar-del/meteogramy';
const EVENT = 'scheduled-update';
const MIN_GAP = 20 * 60;
$tokenFile = dirname(__DIR__, 2) . '/github-dispatch-token.txt';
$stamp = __DIR__ . '/stats/cron-dispatch.json';

header('Content-Type: text/plain; charset=utf-8');
header('Cache-Control: no-store');
header('X-Robots-Tag: noindex');

// Webglobe při přidání úlohy ověřuje existenci skriptu metodou HEAD.
if ($_SERVER['REQUEST_METHOD'] === 'HEAD') exit;

function finish($code, $text) {
    http_response_code($code);
    echo $text, "\n";
    exit;
}

if (!is_dir(dirname($stamp))) mkdir(dirname($stamp), 0755, true);
$fp = fopen($stamp, 'c+');
if (!$fp || !flock($fp, LOCK_EX)) finish(500, 'Nelze zamknout stav.');
$state = json_decode(stream_get_contents($fp), true);
$last = is_array($state) ? (int)($state['last'] ?? 0) : 0;
if (time() - $last < MIN_GAP) finish(200, 'Přeskočeno: poslední spuštění před ' . (time() - $last) . ' s.');

$token = is_readable($tokenFile) ? trim(file_get_contents($tokenFile)) : '';
if (!preg_match('/^github_pat_\w+$/', $token)) finish(500, 'Chybí token mimo public_html.');

// Pokus se zapisuje předem, aby ani opakované chyby nevedly k častějším voláním GitHubu.
ftruncate($fp, 0);
rewind($fp);
fwrite($fp, json_encode(['last' => time(), 'at' => gmdate('c')]));
fflush($fp);

$ch = curl_init('https://api.github.com/repos/' . REPO . '/dispatches');
curl_setopt_array($ch, [
    CURLOPT_POST => true,
    CURLOPT_POSTFIELDS => json_encode(['event_type' => EVENT]),
    CURLOPT_HTTPHEADER => [
        'Authorization: Bearer ' . $token,
        'Accept: application/vnd.github+json',
        'Content-Type: application/json',
        'X-GitHub-Api-Version: 2022-11-28',
        'User-Agent: meteogramy-cron',
    ],
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_TIMEOUT => 20,
]);
$body = curl_exec($ch);
$status = curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
$error = curl_error($ch);

if ($status !== 204) finish(502, "GitHub odpověděl $status. $error " . substr((string)$body, 0, 300));

finish(200, 'Spuštěno ' . gmdate('Y-m-d H:i') . ' UTC.');
