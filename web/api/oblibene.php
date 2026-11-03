<?php
// Oblíbené stanice podle všech návštěvníků.
// POST ?id=stanice  započítá otevření meteogramu (bez IP adresy a cookies, jen denní součty)
// GET               vrátí nejčastěji otevírané stanice za posledních DAYS dní

const DAYS = 30;
const TOP = 8;
$store = __DIR__ . '/stats/views.json';

header('Content-Type: application/json; charset=utf-8');

function load($fp) {
    $raw = stream_get_contents($fp);
    $data = $raw ? json_decode($raw, true) : null;
    return is_array($data) ? $data : [];
}

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    header('Cache-Control: no-store');
    $id = $_GET['id'] ?? '';
    // počítáme jen stanice, které existují v aktuálně publikovaném běhu modelu
    $index = json_decode(@file_get_contents(__DIR__ . '/../data/index.json'), true);
    $run = $index['runs'][0] ?? '';
    if (!preg_match('/^[a-z0-9-]{1,80}$/', $id) || !preg_match('/^20\d{8}$/', $run)
        || !is_file(__DIR__ . "/../data/$run/$id.json")) {
        http_response_code(400);
        echo '{"ok":false}';
        exit;
    }
    if (!is_dir(dirname($store))) mkdir(dirname($store), 0755, true);
    $fp = fopen($store, 'c+');
    flock($fp, LOCK_EX);
    $data = load($fp);
    $day = gmdate('Y-m-d');
    $data[$day][$id] = ($data[$day][$id] ?? 0) + 1;
    // starší dny už do součtu nepatří
    $cutoff = gmdate('Y-m-d', time() - DAYS * 86400);
    foreach (array_keys($data) as $d) if ($d <= $cutoff) unset($data[$d]);
    ftruncate($fp, 0);
    rewind($fp);
    fwrite($fp, json_encode($data));
    fflush($fp);
    flock($fp, LOCK_UN);
    fclose($fp);
    echo '{"ok":true}';
    exit;
}

header('Cache-Control: public, max-age=600');
$totals = [];
if (is_file($store)) {
    $fp = fopen($store, 'r');
    flock($fp, LOCK_SH);
    $data = load($fp);
    flock($fp, LOCK_UN);
    fclose($fp);
    $cutoff = gmdate('Y-m-d', time() - DAYS * 86400);
    foreach ($data as $d => $counts) {
        if ($d <= $cutoff) continue;
        foreach ($counts as $id => $n) $totals[$id] = ($totals[$id] ?? 0) + $n;
    }
}
arsort($totals);
echo json_encode(['days' => DAYS, 'ids' => array_slice(array_keys($totals), 0, TOP)]);
