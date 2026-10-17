// Downloads front (Recto) + back (Verso) specimen images of ID cards from PRADO
// (https://www.consilium.europa.eu/prado/) and hands them to tools/receive.py.
//
// PRADO sits behind a browser check, so plain curl/requests do not work.
// Usage:
//   1. python tools/receive.py data_prado 18765
//   2. open any PRADO page in a normal browser, e.g.
//      https://www.consilium.europa.eu/prado/EN/HRV-BO-04001/index.html
//   3. paste this whole file into the DevTools console and wait for the summary.
(async () => {
  const PORT = 18765;
  const countries = ['HRV', 'SVN', 'AUT', 'DEU', 'ITA', 'HUN', 'SRB', 'BIH', 'MNE', 'MKD', 'ALB', 'POL', 'CZE',
    'SVK', 'ROU', 'BGR', 'GRC', 'ESP', 'PRT', 'FRA', 'BEL', 'NLD', 'LUX', 'IRL', 'EST', 'LVA', 'LTU', 'FIN',
    'SWE', 'DNK', 'CYP', 'MLT', 'CHE', 'NOR', 'ISL', 'LIE', 'UKR', 'MDA', 'TUR', 'XKX'];
  const docs = [];
  await Promise.all(countries.map(async c => {
    // B/O = "identity card" category in PRADO
    const r = await fetch(`/prado/EN/prado-documents/${c}/B/O/docs-per-type.html`);
    if (!r.ok) return;
    const t = await r.text();
    for (const d of new Set([...t.matchAll(/([A-Z]{3}-BO-\d{5})/g)].map(m => m[1]))) {
      const p = await (await fetch(`/prado/EN/${d}/index.html`)).text();
      const imgs = [...p.matchAll(/src="[^"]*\/(\d+)_thumb\.jpg[^"]*"[^>]*alt="Image \d+ of ([^"]*)"/g)]
        .map(m => [m[1], m[2]]);
      const recto = imgs.find(i => /^Recto/i.test(i[1]));
      const verso = imgs.find(i => /^Verso/i.test(i[1]));
      if (recto && verso) docs.push({d, recto: recto[0], verso: verso[0]});
    }
  }));
  const res = {docs: docs.length, saved: 0, failed: []};
  for (const {d, recto, verso} of docs) {
    for (const [id, side] of [[recto, 'front'], [verso, 'back']]) {
      try {
        const blob = await (await fetch(`/prado/images/${d}/${id}.jpg`)).blob();
        const name = `${d.replace(/-/g, '')}_${side}.jpg`;
        const r = await fetch(`http://127.0.0.1:${PORT}/${name}`, {method: 'POST', body: blob});
        if (r.ok) res.saved++; else res.failed.push(`${name} ${r.status}`);
      } catch (e) { res.failed.push(`${d} ${side} ${e}`); }
    }
  }
  console.log(res);
  return res;
})();
