import React, { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react';
import './CurvedInput.css';

const DEG = 180 / Math.PI;
const round2 = n => Math.round(n * 100) / 100;
const SHADOWS = { sm: [5, 12, .3], md: [10, 24, .4], lg: [16, 40, .52] };
const SELECTABLE_TYPES = ['text', 'search', 'tel', 'url', 'password'];

const hexToRgba = (hex, alpha) => {
  let h = String(hex).replace('#', '');
  if (h.length === 3) h = h.split('').map(c => c + c).join('');
  const n = parseInt(h.slice(0, 6), 16);
  if (Number.isNaN(n)) return hex;
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
};

const buildGeometry = (width, bend, thickness, pad) => {
  const W = width, T = thickness;
  const s = Math.max(-W * .35, Math.min(bend, W * .35));
  const a = Math.abs(s), dir = s >= 0 ? 1 : -1;
  const svgH = T + a + pad * 2;
  if (a < .75) {
    const midY = pad + T / 2;
    return { straight: true, W, T, svgH, uPerLen: 1, point: (u, v) => [u, midY + v], angleAt: () => 0, uFromPoint: x => x };
  }
  const R = (W * W * .25 + a * a) / (2 * a), cx = W / 2;
  const apexY = pad + T / 2 + (dir > 0 ? 0 : a), cy = apexY + dir * R;
  const phi = Math.asin(Math.min(1, W / (2 * R)));
  return {
    straight: false, W, T, svgH, R, dir, uPerLen: W / (2 * R * phi),
    point: (u, v) => { const th = ((u - cx) / cx) * phi, rho = R - dir * v; return [cx + rho * Math.sin(th), cy - dir * rho * Math.cos(th)]; },
    angleAt: u => dir * ((u - cx) / cx) * phi * DEG,
    uFromPoint: (x, y) => cx + (Math.atan2(x - cx, dir * (cy - y)) / phi) * cx
  };
};

const fmt = (g, u, v) => { const [x, y] = g.point(u, v); return `${round2(x)} ${round2(y)}`; };
const edgeSeg = (g, uTo, v, ltr) => g.straight ? `L ${fmt(g, uTo, v)}` : `A ${round2(g.R - g.dir * v)} ${round2(g.R - g.dir * v)} 0 0 ${(ltr === g.dir > 0) ? 1 : 0} ${fmt(g, uTo, v)}`;
const bentRectPath = (g, u0, u1, vTop, vBot, radius) => {
  const rc = Math.max(0, Math.min(radius, (vBot - vTop) / 2, (u1 - u0) / 2));
  return [`M ${fmt(g, u0 + rc, vTop)}`, edgeSeg(g, u1 - rc, vTop, true), `Q ${fmt(g, u1, vTop)} ${fmt(g, u1, vTop + rc)}`, `L ${fmt(g, u1, vBot - rc)}`, `Q ${fmt(g, u1, vBot)} ${fmt(g, u1 - rc, vBot)}`, edgeSeg(g, u0 + rc, vBot, false), `Q ${fmt(g, u0, vBot)} ${fmt(g, u0, vBot - rc)}`, `L ${fmt(g, u0, vTop + rc)}`, `Q ${fmt(g, u0, vTop)} ${fmt(g, u0 + rc, vTop)}`, 'Z'].join(' ');
};
const bentLinePath = (g, u0, u1, v) => `M ${fmt(g, u0, v)} ${edgeSeg(g, u1, v, true)}`;

export default function CurvedInput({
  value, defaultValue = '', onChange, onSubmit, eyebrowText = '', placeholder = 'Enter your email', buttonText = 'Get Started', type = 'email', name,
  ariaLabel, ariaDescribedBy, invalid = false, width = 450, bend = 28, height = 64, cornerRadius = 18, borderWidth = 1.5, fontSize = 16,
  backgroundColor = '#1B1722', textColor = '#f5f5f5', placeholderColor = '#a1a1aa', borderColor = '#392e4e',
  buttonColor = '#A855F7', buttonTextColor = '#fff', shadowSize = 'md', shadowColor = '#000', showButton = true,
  showIcon = true, className = '', style
}) {
  const uid = useId().replace(/:/g, ''), layoutPathId = `ci-text-${uid}`, eyebrowPathId = `ci-eyebrow-${uid}`, buttonPathId = `ci-btn-${uid}`, clipId = `ci-clip-${uid}`;
  const rootRef = useRef(null), svgRef = useRef(null), inputRef = useRef(null), textRef = useRef(null), btnMeasureRef = useRef(null), scrollRef = useRef(0);
  const [w, setW] = useState(0), [innerValue, setInnerValue] = useState(defaultValue), [caretIndex, setCaretIndex] = useState(defaultValue.length), [focused, setFocused] = useState(false), [caretU, setCaretU] = useState(0), [scrollLen, setScrollLen] = useState(0), [btnTextW, setBtnTextW] = useState(0);
  const val = value !== undefined ? value : innerValue, display = type === 'password' ? '•'.repeat(val.length) : val;

  useEffect(() => { const el = rootRef.current; if (!el) return; const ro = new ResizeObserver(entries => setW(Math.round(entries[0]?.contentRect?.width ?? el.clientWidth))); ro.observe(el); return () => ro.disconnect(); }, []);
  const pad = Math.ceil(borderWidth / 2) + 6 + (eyebrowText ? 25 : 0);
  const geom = useMemo(() => w > 2 ? buildGeometry(w, bend, height, pad) : null, [w, bend, height, pad]);
  const layout = useMemo(() => { if (!geom) return null; const btnInset = Math.max(5, borderWidth + 4), chipH = Math.min(34, Math.max(16, height * .34)), chipW = chipH * 1.25, textStartU = showIcon ? 22 + chipW + 13 : 24, btnW = showButton ? Math.max(btnTextW + fontSize * 2.7, height * 1.35) : 0, btnU1 = geom.W - btnInset, btnU0 = btnU1 - btnW, textEndU = Math.max(textStartU + 20, showButton ? btnU0 - 14 : geom.W - 24); return { btnInset, textStartU, textEndU, btnU0, btnU1, winLen: (textEndU - textStartU) / geom.uPerLen }; }, [geom, height, borderWidth, btnTextW, fontSize, showIcon, showButton]);

  useLayoutEffect(() => {
    if (btnMeasureRef.current) { const bw = btnMeasureRef.current.getComputedTextLength(); setBtnTextW(prev => Math.abs(prev - bw) > .5 ? bw : prev); }
    if (!geom || !layout) return;
    const textEl = textRef.current, caret = Math.min(caretIndex, display.length); let caretLen = 0, totalLen = 0;
    if (textEl && display.length) { try { totalLen = textEl.getSubStringLength(0, display.length); caretLen = caret > 0 ? textEl.getSubStringLength(0, caret) : 0; } catch {} }
    let next = scrollRef.current; if (caretLen - next > layout.winLen - 2) next = caretLen - layout.winLen + 2; if (caretLen - next < 0) next = caretLen; if (totalLen - next < layout.winLen) next = Math.max(0, totalLen - layout.winLen); next = Math.max(0, next);
    if (Math.abs(next - scrollRef.current) > .5) { scrollRef.current = next; setScrollLen(next); } setCaretU(layout.textStartU + (caretLen - next) * geom.uPerLen);
  });

  const commitValue = v => { if (value === undefined) setInnerValue(v); onChange?.(v); };
  const handleSelect = e => setCaretIndex(e.target.selectionStart ?? e.target.value.length);
  const handleSubmit = e => { e?.preventDefault?.(); onSubmit?.(val); };
  const handleSurfaceClick = e => {
    const input = inputRef.current; if (!input) return; let idx = display.length;
    if (svgRef.current && geom && layout && textRef.current && display.length) { try { const pt = new DOMPoint(e.clientX, e.clientY).matrixTransform(svgRef.current.getScreenCTM().inverse()); const target = scrollRef.current + (geom.uFromPoint(pt.x, pt.y) - layout.textStartU) / geom.uPerLen; let best = 0, bestDist = Infinity; for (let i = 0; i <= display.length; i++) { const len = i === 0 ? 0 : textRef.current.getSubStringLength(0, i), d = Math.abs(len - target); if (d < bestDist) { bestDist = d; best = i; } } idx = best; } catch {} }
    input.focus(); try { input.setSelectionRange(idx, idx); } catch {} setCaretIndex(idx);
  };

  let content = null;
  if (geom && layout) {
    const T = height, vBase = fontSize * .34, scrollU = scrollLen * geom.uPerLen, bandPath = bentRectPath(geom, 0, geom.W, -T / 2, T / 2, cornerRadius), layoutPath = bentLinePath(geom, layout.textStartU - scrollU, geom.W, vBase), eyebrowPath = bentLinePath(geom, 0, geom.W, -T / 2 - 15), clipPath = bentRectPath(geom, layout.textStartU - 6, layout.textEndU + 8, -T / 2, T / 2, 0), caretPoint = geom.point(caretU, 0), caretH = Math.min(T * .58, fontSize * 1.45), buttonPath = showButton ? bentRectPath(geom, layout.btnU0, layout.btnU1, -T / 2 + layout.btnInset, T / 2 - layout.btnInset, cornerRadius * .72) : '', buttonTextPath = showButton ? bentLinePath(geom, layout.btnU0, layout.btnU1, vBase) : '', buttonCenter = geom.point((layout.btnU0 + layout.btnU1) / 2, 0), buttonAngle = geom.angleAt((layout.btnU0 + layout.btnU1) / 2);
    const svgStyle = SHADOWS[shadowSize] ? { filter: `drop-shadow(0 ${SHADOWS[shadowSize][0]}px ${SHADOWS[shadowSize][1]}px ${hexToRgba(shadowColor, SHADOWS[shadowSize][2])})` } : undefined;
    content = <svg ref={svgRef} className="curved-input__svg" width={geom.W} height={round2(geom.svgH)} viewBox={`0 0 ${geom.W} ${round2(geom.svgH)}`} style={svgStyle} onPointerDown={e => e.preventDefault()} onClick={handleSurfaceClick}>
      <defs><clipPath id={clipId}><path d={clipPath}/></clipPath></defs>
      <path className="curved-input__ring" d={bandPath} fill="none" stroke={buttonColor} strokeWidth={borderWidth + 6}/><path d={bandPath} fill={backgroundColor} stroke={borderColor} strokeWidth={borderWidth}/><path id={layoutPathId} d={layoutPath} fill="none"/><path id={eyebrowPathId} d={eyebrowPath} fill="none"/>{eyebrowText && <text className="curved-input__eyebrow" fill="rgba(255,248,230,.8)" textAnchor="middle"><textPath href={`#${eyebrowPathId}`} startOffset="50%">{eyebrowText}</textPath></text>}
      <g clipPath={`url(#${clipId})`}><text ref={textRef} style={{fontSize, fontWeight:500}} fill={textColor} xmlSpace="preserve" aria-hidden="true"><textPath href={`#${layoutPathId}`}>{display}</textPath></text>{!display && placeholder && <text style={{fontSize, fontWeight:500}} fill={placeholderColor} xmlSpace="preserve" aria-hidden="true"><textPath href={`#${layoutPathId}`}>{placeholder}</textPath></text>}{focused && <g transform={`translate(${round2(caretPoint[0])} ${round2(caretPoint[1])}) rotate(${round2(geom.angleAt(caretU))})`}><line y1={-caretH/2} y2={caretH/2} stroke={textColor} strokeWidth="1.5" strokeLinecap="round"><animate attributeName="opacity" values="1;0" dur="1.06s" calcMode="discrete" repeatCount="indefinite"/></line></g>}</g>
      {showButton && <g className="curved-input__button" role="button" tabIndex="0" aria-label={buttonText} onClick={e => { e.stopPropagation(); handleSubmit(); }} onPointerDown={e => e.stopPropagation()} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleSubmit(); } }}><path className="curved-input__button-bg" d={buttonPath} fill={buttonColor}/><path id={buttonPathId} d={buttonTextPath} fill="none"/><text x={round2(buttonCenter[0])} y={round2(buttonCenter[1])} fill={buttonTextColor} textAnchor="middle" dominantBaseline="central" transform={`rotate(${round2(buttonAngle)} ${round2(buttonCenter[0])} ${round2(buttonCenter[1])})`} style={{fontSize:fontSize+8,fontWeight:400,pointerEvents:'none'}}>{buttonText}</text></g>}
      <text ref={btnMeasureRef} style={{fontSize,fontWeight:600}} x="-9999" y="-9999" visibility="hidden" aria-hidden="true">{buttonText}</text>
    </svg>;
  }

  const safeType = SELECTABLE_TYPES.includes(type) ? type : 'text';
  return <form ref={rootRef} className={`curved-input ${focused ? 'curved-input--focused' : ''} ${invalid ? 'curved-input--invalid' : ''} ${className}`.trim()} style={{width:typeof width==='number'?`${width}px`:width,...style}} onSubmit={handleSubmit} noValidate>{content}<input ref={inputRef} className="curved-input__field" type={safeType} inputMode={type==='number'?'decimal':undefined} name={name} value={val} onChange={e=>{commitValue(e.target.value);handleSelect(e)}} onSelect={handleSelect} onKeyDown={e=>{if(e.key==='Enter')handleSubmit(e)}} onKeyUp={handleSelect} onFocus={()=>setFocused(true)} onBlur={()=>setFocused(false)} aria-label={ariaLabel||placeholder||'Curved input'} aria-describedby={ariaDescribedBy} aria-invalid={invalid} autoComplete="tel" autoCapitalize="none" autoCorrect="off" spellCheck={false}/></form>;
}
