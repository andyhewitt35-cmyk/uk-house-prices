// usage: node test.js <url> <prefix: local|live>
const { chromium } = require('playwright-core');
const URL_ = process.argv[2] || 'http://127.0.0.1:18937/';
const PRE = process.argv[3] || 'local';
let fails = 0; const ok = (c, m) => { console.log((c ? 'PASS ' : 'FAIL ') + m); if (!c) fails++; };
const NEAR = {Parbold: [53.5920, -2.7708], Orrell: [53.5300, -2.7080], Wigan: [53.5450, -2.6325], Manchester: [53.4808, -2.2426], London: [51.5074, -0.1278]};
const SEARCH = ['WN8 7AA', 'WN5 7XA', 'Wigan', 'Manchester', 'London', 'Mill Lane, Parbold', 'WN8', 'EH1 1YZ'];
(async () => {
  const b = await chromium.launch({executablePath: '/usr/bin/google-chrome', headless: true});
  for (const [w, h, name] of [[390, 844, 'mobile'], [1280, 900, 'desktop']]) {
    const ctx = await b.newContext({viewport: {width: w, height: h}, deviceScaleFactor: name === 'mobile' ? 2 : 1, isMobile: name === 'mobile', hasTouch: name === 'mobile',
      permissions: ['geolocation'], geolocation: {latitude: NEAR.Parbold[0], longitude: NEAR.Parbold[1]}, timezoneId: 'Europe/London', locale: 'en-GB'});
    const p = await ctx.newPage(); const errs = [], bad = [];
    p.on('console', m => { if (m.type() === 'error') errs.push(m.text()); }); p.on('pageerror', e => errs.push(e.message));
    p.on('response', r => { if (r.status() >= 400) bad.push(r.status() + ' ' + r.url().slice(0, 120)); });
    if (name === 'mobile') { const cdp = await ctx.newCDPSession(p); await cdp.send('Network.enable');
      await cdp.send('Network.emulateNetworkConditions', {offline: false, latency: 150, downloadThroughput: 9e6 / 8, uploadThroughput: 3e6 / 8});
      await cdp.send('Emulation.setCPUThrottlingRate', {rate: 4}); }
    const t0 = Date.now();
    await p.goto(URL_, {waitUntil: 'domcontentloaded'});
    await p.waitForFunction(() => /Tap Near me|Couldn't load|Showing/.test(document.getElementById('placeLine').textContent), null, {timeout: 30000});
    ok(/Tap Near me/.test(await p.textContent('#placeLine')), `${name}: data loaded in ${Date.now() - t0} ms`);
    ok(/Latest sale in the data/.test(await p.textContent('#fresh')), `${name}: freshness: ${(await p.textContent('#fresh')).slice(0, 200)}`);
    const run = async (label, act) => {
      const seq = await p.evaluate(() => window.__placeSeq || 0);
      await act();
      const done = await p.waitForFunction(q => (window.__placeSeq || 0) > q || /err/.test(document.getElementById('placeLine').className), seq, {timeout: 25000}).then(() => true).catch(() => false);
      const line = (await p.textContent('#placeLine')).replace(/\s*clear$/, '').trim();
      const g = await p.evaluate(() => window.__hp);
      return {done, line, g};
    };
    for (const [city, ll] of Object.entries(NEAR)) {
      await ctx.setGeolocation({latitude: ll[0], longitude: ll[1]});
      const {done, line, g} = await run(city, () => p.click('#nearBtn'));
      ok(done && g && g.median > 0 && g.sales > 0 && g.trend, `${name}: Near me ${city} -> ${line} | ${g && g.district}${g && g.sector ? ' / ' + g.sector : ''}: median £${g && g.median}, n=${g && g.n}, headline ${g && JSON.stringify(g.headline)}, trend ${g && JSON.stringify(g.trend)}, sector trend ${g && JSON.stringify(g.sectorTrend)}, ${g && g.sales} sales within ½ mi`);
      if (city === 'Parbold') {
        console.log('      nearest: ' + JSON.stringify(g.nearest));
        await p.evaluate(() => window.scrollTo(0, 0));
        await p.screenshot({path: PRE === 'live' ? (name === 'mobile' ? 'shots/mobile-live.png' : 'shots/desktop-live.png') : `shots/${PRE}-${name}-parbold.png`});
        if (name === 'desktop') await p.screenshot({path: `shots/${PRE}-desktop-parbold-full.png`, fullPage: true});
        else await p.screenshot({path: `shots/${PRE}-mobile-parbold-full.png`, fullPage: true});
        // controls
        for (const t of ['D', 'F', 'a']) { await p.click(`#typeBtns button[data-v="${t}"]`); await p.waitForTimeout(200); }
        for (const r of ['1', '3', '5']) { await p.click(`#rangeBtns button[data-v="${r}"]`); await p.waitForTimeout(200); }
        const pathN = await p.$$eval('#chart path', e => e.length); ok(pathN >= 2, `${name}: chart lines ${pathN}`);
        const bars = await p.$$eval('#bars .bar', e => e.map(x => x.innerText.replace(/\s+/g, ' ')));
        ok(bars.length >= 4, `${name}: compare bars ${JSON.stringify(bars)}`);
        console.log('      ' + (await p.textContent('#cmpText')) + ' | ' + (await p.textContent('#hpiBox')).slice(0, 220));
        for (const r of ['0.25', '1', '2']) { await p.click(`#radBtns button[data-v="${r}"]`); await p.waitForTimeout(300); console.log(`      radius ${r} mi: ${(await p.evaluate(() => window.__hp.sales))} sales`); }
        await p.click('#sortBtns button[data-v="hi"]'); await p.waitForTimeout(300);
        const top = await p.evaluate(() => window.__hp.nearest[0]); console.log('      priciest within 2 mi: ' + JSON.stringify(top));
        await p.click('#sTypeBtns button[data-v="T"]'); await p.waitForTimeout(300);
        const tt = await p.evaluate(() => window.__hp.nearest.every(x => x.t === 'T')); ok(tt, `${name}: type filter terraced`);
        await p.click('#sTypeBtns button[data-v=""]'); await p.click('#sortBtns button[data-v="new"]'); await p.click('#radBtns button[data-v="0.5"]'); await p.waitForTimeout(300);
        const tags = await p.$$eval('#sales .sale', e => e.slice(0, 3).map(x => x.innerText.replace(/\s+/g, ' ')));
        ok(tags.length && tags.every(t => /Freehold|Leasehold/.test(t) && /(Detached|Semi|Terraced|Flat)/.test(t)), `${name}: sale rows ${JSON.stringify(tags)}`);
      }
    }
    for (const q of SEARCH) {
      const {done, line, g} = await run(q, async () => { await p.fill('#placeInput', q); await p.click('#placeGo'); });
      if (q === 'EH1 1YZ') { ok(/England and Wales only/.test(line), `${name}: Scotland postcode -> ${line}`); continue; }
      ok(done && g && g.median > 0 && /^Showing/.test(line), `${name}: search "${q}" -> ${line} | ${g && g.district}${g && g.sector ? ' / ' + g.sector : ''}: median £${g && g.median}, headline ${g && JSON.stringify(g.headline)}, trend ${g && JSON.stringify(g.trend)}, ${g && g.sales} sales nearby`);
    }
    await p.reload({waitUntil: 'domcontentloaded'}); await p.waitForFunction(() => window.__hp, null, {timeout: 20000}).catch(() => {});
    ok(/last used/.test(await p.textContent('#placeLine')), `${name}: last place remembered`);
    if (name === 'mobile') { const sw = await p.evaluate(() => document.documentElement.scrollWidth); ok(sw <= 392, `mobile: no sideways scroll (${sw})`); }
    ok(errs.length === 0, `${name}: no console errors ${errs.join(' | ')}`);
    ok(bad.length === 0, `${name}: no failed requests ${bad.join(' | ')}`);
    await ctx.close();
  }
  await b.close(); console.log(fails ? `${fails} FAILED` : 'ALL PASSED'); process.exit(fails ? 1 : 0);
})();
