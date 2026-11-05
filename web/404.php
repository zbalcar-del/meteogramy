<?php
// Stránka pro neexistující adresy (viz .htaccess): vygenerovaná 404.html se správným kódem 404.
http_response_code(404);
header('Content-Type: text/html; charset=utf-8');
header('Cache-Control: no-cache');
header('X-Robots-Tag: noindex');
readfile(__DIR__ . '/404.html');
