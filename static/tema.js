// Aplica o tema salvo antes da pintura (evita o "flash" do tema errado). Script clássico em <head>, sem inline (CSP: script-src 'self').
// Valores gravados em localStorage "reqemp.tema": "claro" | "escuro"; ausente = segue o sistema (prefers-color-scheme).
(function () {
  try {
    var t = localStorage.getItem('reqemp.tema');
    if (t === 'claro') document.documentElement.setAttribute('data-theme', 'light');
    else if (t === 'escuro') document.documentElement.setAttribute('data-theme', 'dark');
  } catch (e) { /* sem armazenamento: segue o sistema */ }
})();
