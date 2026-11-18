// Word-by-word captions for Shadow 2D scenes. PURE function of t (seek-safe, no Math.random, no state).
// Usage: Captions.draw(ctx, W, H, t, WORDS.words, {style:'pop'|'karaoke', vertical:false})
window.Captions = (function () {
  function groups(words, maxChars, gap) {           // phrase groups: break on length or pause
    const out = []; let cur = [];
    for (let i = 0; i < words.length; i++) {
      const w = words[i], prev = words[i - 1];
      const len = cur.map(x => x.w).join(' ').length + w.w.length + 1;
      if (cur.length && (len > maxChars || (prev && w.s - prev.e > gap) || /[.?!]$/.test(prev.w))) { out.push(cur); cur = []; }
      cur.push(w);
    }
    if (cur.length) out.push(cur); return out;
  }
  const ease = x => 1 - Math.pow(1 - Math.min(1, Math.max(0, x)), 3);
  function draw(ctx, W, H, t, words, o) {
    o = Object.assign({ style: 'pop', vertical: false, font: 'Poppins, Arial, sans-serif', color: '#ffffff', hi: '#ffd23f', maxChars: 26, gap: 0.45, hold: 0.25 }, o || {});
    const G = groups(words, o.vertical ? 18 : o.maxChars, o.gap);
    const g = G.find(gr => t >= gr[0].s - 0.05 && t <= gr[gr.length - 1].e + o.hold); if (!g) return;
    const size = Math.round((o.vertical ? W * 0.085 : H * 0.075));
    const y = o.vertical ? H * 0.70 : H * (o.yh || 0.88);           // keeps clear of Shorts bottom UI (~20%)
    ctx.save(); ctx.font = `800 ${size}px ${o.font}`; ctx.textBaseline = 'middle'; ctx.lineJoin = 'round';
    const gapW = size * 0.28, ws = g.map(w => ctx.measureText(w.w).width);
    const total = ws.reduce((a, b) => a + b, 0) + gapW * (g.length - 1); let x = (W - total) / 2;
    g.forEach((w, i) => {
      const active = t >= w.s && t < (g[i + 1] ? g[i + 1].s : w.e + o.hold), shown = t >= w.s || o.style === 'karaoke';
      if (shown) {
        const k = ease((t - w.s) / 0.12), sc = o.style === 'pop' && active ? 1 + 0.12 * (1 - k) + 0.04 : 1;
        ctx.save(); ctx.translate(x + ws[i] / 2, y); ctx.scale(sc, sc);
        ctx.lineWidth = size * 0.16; ctx.strokeStyle = 'rgba(0,0,0,0.85)'; ctx.textAlign = 'center';
        ctx.strokeText(w.w, 0, 0); ctx.fillStyle = active ? o.hi : o.color; ctx.fillText(w.w, 0, 0); ctx.restore();
      }
      x += ws[i] + gapW;
    });
    ctx.restore();
  }
  return { draw, groups };
})();
