import { useEffect, useRef, useState } from 'react';
import { ChevronDown, Upload } from 'lucide-react';
import { Button } from '../lab/ui';
import { studio } from './api';
import { useStudio } from './store';
import type { ParseResult } from './types';
import s from './studio.module.css';

/** One sentence to a task map: live parse, examples, and GeoJSON or scenario import. */
export function ComposeBar() {
  const text = useStudio((x) => x.composeText);
  const setText = useStudio((x) => x.setComposeText);
  const compose = useStudio((x) => x.composeFromText);
  const busy = useStudio((x) => x.busy);
  const notify = useStudio((x) => x.notify);
  const [focused, setFocused] = useState(false);
  const [parse, setParse] = useState<ParseResult | null>(null);
  const [examples, setExamples] = useState<string[]>([]);
  const [showExamples, setShowExamples] = useState(false);
  const file = useRef<HTMLInputElement>(null);
  useEffect(() => {
    studio.examples().then((r) => setExamples(r.examples)).catch(() => undefined);
  }, []);
  useEffect(() => {
    if (!text.trim()) {
      setParse(null);
      return;
    }
    const t = window.setTimeout(() => studio.parse(text).then(setParse).catch(() => setParse(null)), 220);
    return () => window.clearTimeout(t);
  }, [text]);
  const found = new Set((parse?.found ?? []).map((f) => f.key.split('.')[0]));
  async function onFile(f: File) {
    try {
      const obj = JSON.parse(await f.text());
      const sc = obj.type === 'FeatureCollection' || obj.type === 'Feature' ? await studio.importGeojson(obj, f.name.replace(/\.(geo)?json$/i, '')) : await studio.importScenario(obj);
      location.hash = `#/plan/${encodeURIComponent(sc.id)}`;
      notify('ok', `已导入 ${sc.title}`);
    } catch (e) {
      notify('error', `导入失败：${(e as Error).message}`);
    }
  }
  return (
    <div className={s.compose}>
      <div className={s.composeField} data-focus={focused || undefined}>
        <input
          value={text}
          placeholder="用一句话描述任务，例如：在 200×120 米的果园里，3 台机器人从西南角出发采样 40 个点，避开两个池塘"
          onChange={(e) => setText(e.target.value)}
          onFocus={() => setFocused(true)}
          onBlur={() => window.setTimeout(() => { setFocused(false); setShowExamples(false); }, 160)}
          onKeyDown={(e) => { if (e.key === 'Enter') { (e.target as HTMLInputElement).blur(); void compose(); } }}
          aria-label="任务描述"
        />
        <button type="button" className={s.examplesBtn} title="示例" onClick={() => setShowExamples((v) => !v)}><ChevronDown size={14} /></button>
        {(focused || showExamples) && (parse || showExamples || !text) && (
          <div className={s.composePop}>
            {parse && text ? (
              <>
                <div className={s.popHead}>将生成</div>
                <div className={s.popCards}>
                  {parse.cards.map((c) => (
                    <span key={c.key} className={s.popCard} data-default={!found.has(c.key) || undefined}><em>{c.label}</em>{c.value}</span>
                  ))}
                </div>
                <div className={s.popHint}>深色项来自你的描述，浅色项为默认值，生成后都可以在地图和检查器里修改。按回车生成。</div>
              </>
            ) : null}
            {(showExamples || !text) && examples.length > 0 && (
              <>
                <div className={s.popHead}>试试这些描述</div>
                {examples.map((ex) => (
                  <button key={ex} type="button" className={s.example} onMouseDown={(e) => { e.preventDefault(); setText(ex); setShowExamples(false); }}>{ex}</button>
                ))}
              </>
            )}
          </div>
        )}
      </div>
      <Button variant="primary" loading={busy === '生成地图'} disabled={!text.trim()} onClick={() => { setFocused(false); void compose(); }}>生成地图</Button>
      <Button icon={Upload} title="导入 GeoJSON 或场景 JSON" onClick={() => file.current?.click()}>导入</Button>
      <input ref={file} type="file" accept=".json,.geojson,application/json" hidden onChange={(e) => e.target.files?.[0] && void onFile(e.target.files[0])} />
    </div>
  );
}
