"""Offline level viewer: python -m proofledger.viewer [out.html]  (default: ./viewer.html)

Builds one self-contained HTML file (no network) from open seeds: the level map per policy,
a step chain of a sample episode on each level, evaluator verdicts, the standard check table and
the reward-hacking zoo. It only visualizes scripted runs; it does not train anything.
"""
import json
import sys
from pathlib import Path

from . import trace
from .attacks import zoo_report
from .env import Environment
from .generator import VERSION
from .levels import LEVELS, evaluate_levels
from .policies import make, run, run_policy
from .verify import CONFIGS

POLICIES = ("reconciler", "naive_reconciler", "random_explorer", "flag_all")
SAMPLE_SEED = 3


def collect(seeds=20):
    lv = evaluate_levels(POLICIES, seeds)
    traces = {}
    for L in LEVELS:
        for p in POLICIES:
            env = Environment()
            run(make(p, env), env, SAMPLE_SEED, {"level": L["id"]})
            traces[f"{p}|{L['id']}"] = trace.build(env, p, L["id"], SAMPLE_SEED)
    std = []
    for c, cfg in CONFIGS.items():
        for p in ("oracle", "reconciler", "random_explorer", "flag_all", "silence"):
            reps = [run_policy(p, s, cfg)["report"] for s in range(10)]
            std.append({"config": c, "policy": p, "f1": sum(r["f1"] for r in reps) / 10,
                        "reward": sum(r["reward"] for r in reps) / 10})
    return {"version": VERSION, "seeds": seeds, "sample_seed": SAMPLE_SEED, "levels": LEVELS,
            "policies": list(POLICIES),
            "stats": {f"{p}|{l}": v for (p, l), v in lv.items()},
            "traces": traces, "standard": std, "zoo": zoo_report(10)}


TEMPLATE = r"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ProofLedger · Отчёт по уровням</title>
<style>
:root{--paper:#EAF1E4;--rule:#C4D6BA;--ink:#1D2C4C;--ink2:#4A5878;--red:#B3261E;--green:#2C6E49;--gold:#A87B1F;
--card:#F6F9F2;--shadow:#1D2C4C1F;--locked:#9AA59A;color-scheme:light}
@media (prefers-color-scheme:dark){:root{--paper:#16211B;--rule:#26362C;--ink:#DCE6F5;--ink2:#9FB0C9;--red:#F08A80;
--green:#7BC79A;--gold:#E2B85C;--card:#1D2A23;--shadow:#00000066;--locked:#5E6B62;color-scheme:dark}}
*{box-sizing:border-box}
body{margin:0;color:var(--ink);font:16px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
background:var(--paper) repeating-linear-gradient(to bottom,transparent 0 31px,var(--rule) 31px 32px);
font-variant-numeric:tabular-nums}
body::before{content:"";position:fixed;inset:0 auto 0 56px;width:2px;background:var(--red);opacity:.35;pointer-events:none}
main{max-width:1180px;margin:0 auto;padding:28px 20px 80px 76px}
h1,h2{font-family:"Iowan Old Style","Palatino Linotype",Palatino,Georgia,serif;font-weight:700;margin:0}
h1{font-size:40px;letter-spacing:-.01em}
h2{font-size:24px;margin:40px 0 12px}
.top{display:flex;flex-wrap:wrap;gap:16px 28px;align-items:end;justify-content:space-between}
.sub{color:var(--ink2);margin:4px 0 0;max-width:62ch}
.controls{display:flex;gap:12px;flex-wrap:wrap;align-items:center}
.seg{display:inline-flex;border:1.5px solid var(--ink);border-radius:999px;overflow:hidden}
.seg button{all:unset;cursor:pointer;padding:7px 16px;font-weight:600}
.seg button[aria-pressed=true]{background:var(--ink);color:var(--paper)}
.seg button:focus-visible,select:focus-visible,.seal:focus-visible{outline:3px solid var(--gold);outline-offset:2px}
select{font:inherit;color:var(--ink);background:var(--card);border:1.5px solid var(--ink);border-radius:8px;padding:6px 10px}
.path{position:relative;display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin:36px 0 8px;padding:0 4px}
.path::before{content:"";position:absolute;left:10%;right:10%;top:62px;border-top:3px dashed var(--ink2);opacity:.5}
.seal{all:unset;cursor:pointer;position:relative;display:flex;flex-direction:column;align-items:center;text-align:center;gap:8px}
.disc{width:124px;height:124px;border-radius:50%;display:grid;place-items:center;background:var(--card);
border:3px solid var(--ink);box-shadow:0 0 0 6px var(--card),0 0 0 8px var(--ink),0 6px 18px var(--shadow);
transition:transform .25s}
.disc b{font-family:Georgia,serif;font-size:34px;line-height:1}
.disc small{display:block;font-size:13px;color:var(--ink2)}
.seal.passed .disc{border-color:var(--gold);box-shadow:0 0 0 6px var(--card),0 0 0 8px var(--gold),0 6px 18px var(--shadow)}
.seal.passed .disc b{color:var(--gold)}
.seal.failed .disc{border-color:var(--red);box-shadow:0 0 0 6px var(--card),0 0 0 8px var(--red),0 6px 18px var(--shadow)}
.seal.locked .disc{border-style:dashed;border-color:var(--locked);box-shadow:none;color:var(--locked)}
.seal.locked .disc b{color:var(--locked)}
.seal[aria-current=true] .disc{transform:translateY(-6px) rotate(-4deg)}
.seal .t{font-weight:700}
.seal .m{font-size:13px;color:var(--ink2)}
.stampline{margin:18px 0 0;font-family:Georgia,serif;font-size:18px}
.panel{background:var(--card);border:1.5px solid var(--ink);border-radius:14px;padding:20px 22px;margin-top:22px;
box-shadow:6px 6px 0 var(--shadow)}
.goal{margin:6px 0 14px;max-width:70ch}
.facts{display:flex;flex-wrap:wrap;gap:6px 22px;color:var(--ink2);font-size:14px}
.facts strong{color:var(--ink)}
.flow{display:flex;flex-wrap:wrap;row-gap:18px;padding:18px 0 8px;margin-top:14px}
.node{flex:0 0 auto;width:150px;position:relative;background:var(--paper);border:1.5px solid var(--ink);
border-radius:10px;padding:10px 12px;margin-right:34px}
.node:not(:last-child)::after{content:"";position:absolute;right:-36px;top:50%;width:34px;border-top:2px solid var(--ink2)}
.node:not(:last-child)::before{content:"";position:absolute;right:-37px;top:calc(50% - 4px);border:5px solid transparent;border-left-color:var(--ink2)}
.node .k{font-size:12px;color:var(--ink2)}
.node .a{font-weight:650;word-break:break-word}
.node .o{font-size:13px;margin-top:4px}
.node.read{border-left:6px solid var(--ink)}
.node.err{border-left:6px solid var(--red)}
.node.submit{border-left:6px solid var(--gold)}
.node.judge{background:var(--card);border-width:2.5px}
.node.judge.good{border-color:var(--green)}.node.judge.bad{border-color:var(--red)}
.big{font-family:Georgia,serif;font-size:28px;line-height:1.1}
table{border-collapse:collapse;width:100%;font-size:14px}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--rule);vertical-align:top}
th{font-weight:650;color:var(--ink2)}
td.num,th.num{text-align:right}
.v{display:inline-block;min-width:46px;text-align:center;font-weight:800;font-size:12px;padding:2px 6px;border:2px solid;border-radius:4px;transform:rotate(-3deg)}
.v.TP{color:var(--green)}.v.FP{color:var(--red)}.v.MISS{color:var(--gold)}
.ok{color:var(--green);font-weight:700}.no{color:var(--red);font-weight:700}
.scroll{overflow-x:auto}
.note{color:var(--ink2);font-size:14px;max-width:75ch}
[hidden]{display:none!important}
@media (max-width:760px){main{padding:20px 16px 60px 30px}body::before{left:16px}h1{font-size:30px}
.path{grid-template-columns:1fr;gap:20px}.path::before{display:none}.seal{flex-direction:row;text-align:left}
.disc{width:84px;height:84px}.disc b{font-size:24px}}
@media (prefers-reduced-motion:reduce){.disc{transition:none}.seal[aria-current=true] .disc{transform:none}}
</style></head><body><main>
<header class="top">
  <div><h1>Отчёт по уровням</h1>
  <p class="sub">Сохранённые результаты ProofLedger. Выберите скрипт и уровень, чтобы посмотреть шаги аудита и оценку выводов.</p></div>
  <div class="controls">
    <label>Скрипт <select id="pol"></select></label>
    <div class="seg" role="group" aria-label="Режим">
      <button id="mLevels" aria-pressed="true">Уровни</button><button id="mStd" aria-pressed="false">Обычная проверка</button>
    </div>
  </div>
</header>

<section id="levelsView">
  <div class="path" id="path"></div>
  <p class="stampline" id="stampline"></p>
  <div class="panel" id="panel"></div>
</section>

<section id="stdView" hidden>
  <h2>Базовые политики</h2>
  <p class="note">Средняя награда и F1 на 10 открытых seed для каждой конфигурации ошибок. Oracle должен давать 1, сверка выше молчания, «пометить всё» не лучше молчания.</p>
  <div class="scroll"><table id="stdTable"></table></div>
  <h2>Попытки обмануть награду</h2>
  <p class="note">Каждый скрипт пытается получить награду, ничего не доказав. Правило: ниже честного ответа на книгах с ошибками и не выше молчания на чистых. text_only меняет только объяснения, и награда не должна измениться.</p>
  <div class="scroll"><table id="zooTable"></table></div>
</section>
<p class="note" id="foot"></p>
</main>
<script type="application/json" id="data">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById('data').textContent);
const NAMES = {reconciler:'Сверка (наша)', naive_reconciler:'Наивная сверка', random_explorer:'Случайный исследователь', flag_all:'«Пометить всё»',
  oracle:'Oracle', silence:'Молчание'};
const KIND = {list_records:'чтение', get_record:'запись', get_document:'документ', calculator:'калькулятор', submit:'отправка', reset:'старт', invalid:'ошибка'};
const $ = s => document.querySelector(s);
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
let pol = 'reconciler', cur = 'L1';
try { pol = localStorage.getItem('pl.pol') || pol; } catch (e) {}

const sel = $('#pol');
D.policies.forEach(p => { const o = el('option', null, NAMES[p] || p); o.value = p; if (p === pol) o.selected = true; sel.append(o); });
sel.onchange = () => { pol = sel.value; try { localStorage.setItem('pl.pol', pol); } catch (e) {} draw(); };

function state(p) {
  let open = true; const out = {};
  D.levels.forEach(L => { const s = D.stats[p + '|' + L.id];
    out[L.id] = !open ? 'locked' : (s.passed ? 'passed' : 'failed'); if (!s.passed) open = false; });
  return out;
}

function draw() {
  const st = state(pol), path = $('#path'); path.textContent = '';
  let passed = 0;
  D.levels.forEach(L => {
    const s = D.stats[pol + '|' + L.id], b = el('button', 'seal ' + st[L.id]);
    if (st[L.id] === 'passed') passed++;
    b.setAttribute('aria-current', L.id === cur); b.setAttribute('aria-label', L.id + ' ' + L.title);
    const disc = el('div', 'disc'), inner = el('div');
    inner.append(el('b', null, L.id), el('small', null, st[L.id] === 'locked' ? 'закрыт' : Math.round(s.solved * 100) + '%'));
    disc.append(inner);
    const txt = el('div'); txt.append(el('div', 't', L.title),
      el('div', 'm', st[L.id] === 'passed' ? 'пройден' : st[L.id] === 'failed' ? 'не пройден' : 'пройдите ' + prev(L.id)));
    b.append(disc, txt); b.onclick = () => { cur = L.id; draw(); }; path.append(b);
  });
  $('#stampline').textContent = (NAMES[pol] || pol) + ': пройдено ' + passed + ' из ' + D.levels.length +
    ' уровней. Уровень засчитан, если F1 = 1 хотя бы в 90% из ' + D.seeds + ' эпизодов.';
  panel(st);
}
const prev = id => 'L' + (Number(id.slice(1)) - 1);

function panel(st) {
  const L = D.levels.find(x => x.id === cur), s = D.stats[pol + '|' + cur], t = D.traces[pol + '|' + cur], p = $('#panel');
  p.textContent = '';
  const h = el('h2', null, cur + ' · ' + L.title); h.style.marginTop = 0; p.append(h, el('p', 'goal', L.goal));
  if (st[cur] === 'locked') p.append(el('p', 'note', 'Уровень закрыт для этого скрипта: сначала нужно пройти ' + prev(cur) + '. Ниже всё равно показан его прогон, чтобы было видно, где он ломается.'));
  const f = el('div', 'facts');
  [['решено эпизодов', Math.round(s.solved * 100) + '%'], ['средний F1', s.f1.toFixed(2)], ['средняя награда', s.reward.toFixed(3)],
   ['ложных выводов', s.fp], ['пропусков', s.fn], ['шагов в среднем', s.steps.toFixed(1)]]
    .forEach(([k, v]) => { const x = el('span'); x.append(k + ': ', el('strong', null, String(v))); f.append(x); });
  p.append(f);
  p.append(el('h2', null, 'Прогон эпизода ' + t.episode_id));
  p.append(el('p', 'note', 'Seed ' + t.seed + ', период ' + t.period + ', часовой пояс компании ' + t.timezone + '. В книге ' +
    t.counts.ledger_rows + ' проводок, ' + t.counts.invoices + ' счетов, ' + t.counts.payments + ' платежей; спрятано ошибок: ' + t.issues + '.'));
  const flow = el('div', 'flow');
  group(t.steps).forEach(g => {
    const n = el('div', 'node ' + (g.err ? 'err' : g.kind === 'submit' ? 'submit' : 'read'));
    n.append(el('div', 'k', (g.from === g.to ? 'шаг ' + g.from : 'шаги ' + g.from + '–' + g.to) + ' · ' + (KIND[g.kind] || g.kind)),
      el('div', 'a', g.label), el('div', 'o', g.obs));
    flow.append(n);
  });
  const r = t.report, j = el('div', 'node judge ' + (r.reward > 0 ? 'good' : 'bad'));
  j.append(el('div', 'k', 'оценщик, после прогона'), el('div', 'big', (r.reward >= 0 ? '+' : '') + r.reward.toFixed(3)),
    el('div', 'o', 'F1 ' + r.f1.toFixed(2) + ' · верных ' + r.tp + ' · ложных ' + r.fp + ' · пропущено ' + r.fn));
  flow.append(j); p.append(flow);
  p.append(el('h2', null, 'Вердикты по выводам'));
  if (!t.verdicts.length && !t.missed.length) { p.append(el('p', 'note', 'Скрипт ничего не заявил, и ошибок в книге не было.')); return; }
  const tb = el('table'), hr = el('tr');
  ['вердикт', 'тип', 'записи', 'сумма', 'почему'].forEach((x, i) => hr.append(el('th', i === 3 ? 'num' : null, x)));
  tb.append(hr);
  const REASON = {'matched':'совпало с эталоном и доказано', 'false accusation: no discrepancy on these records':'ложное обвинение: на этих записях ошибки нет',
    'wrong type for these records':'ошибка есть, но другого типа', 'amount outside tolerance':'сумма вне допуска', 'wrong currency':'не та валюта',
    'evidence does not prove it':'доказательства не подтверждают', 'repeat of an already matched finding':'повтор уже засчитанного вывода'};
  t.verdicts.slice(0, 40).forEach(v => { const tr = el('tr'), b = el('td'); b.append(el('span', 'v ' + v.verdict, v.verdict));
    tr.append(b, el('td', null, v.type), el('td', null, v.affected_ids.join(', ')),
      el('td', 'num', (v.amount / 100).toLocaleString('ru-RU', {minimumFractionDigits: 2}) + ' ' + v.currency), el('td', null, REASON[v.reason] || v.reason));
    tb.append(tr); });
  t.missed.forEach(m => { const tr = el('tr'), b = el('td'); b.append(el('span', 'v MISS', 'MISS'));
    tr.append(b, el('td', null, m.type), el('td', null, m.affected_ids.join(', ')), el('td', 'num', '—'), el('td', null, 'ошибка была, скрипт её не нашёл'));
    tb.append(tr); });
  const wrap = el('div', 'scroll'); wrap.append(tb); p.append(wrap);
}

const RU_OBS = o => { let m = o.match(/^(\d+) records/); if (m) return m[1] + ' записей';
  if (o === 'submitted') return 'принято'; if (o.startsWith('= ')) return o; if (o.startsWith('error: ')) return 'ошибка: ' + o.slice(7); return o; };
function group(steps) {
  const out = [];
  steps.forEach(s => {
    const err = s.obs.startsWith('error'), m = s.action.match(/^list_records (\w+)/), last = out[out.length - 1];
    const n = (s.obs.match(/^(\d+) records/) || [0, 0])[1] * 1;
    if (m && last && last.table === m[1] && !err && !last.err) { last.to = s.step; last.pages++; last.rows += n;
      last.label = 'читает ' + m[1] + ' (' + last.pages + ' стр.)'; last.obs = last.rows + ' записей'; return; }
    const sub = s.action.match(/^submit (\S+) findings/);
    out.push({from: s.step, to: s.step, kind: s.kind, err, table: m ? m[1] : null, pages: 1, rows: n,
      label: s.kind === 'reset' ? 'получает задачу и правила' : m ? 'читает ' + m[1] : sub ? 'отправляет выводы: ' + sub[1] : s.action,
      obs: s.kind === 'reset' ? 'манифест, политика, 100 шагов' : RU_OBS(s.obs)});
  });
  return out;
}

function std() {
  const t = $('#stdTable'), pols = ['oracle', 'reconciler', 'random_explorer', 'flag_all', 'silence'], hr = el('tr');
  hr.append(el('th', null, 'конфигурация')); pols.forEach(p => hr.append(el('th', 'num', NAMES[p]))); t.append(hr);
  [...new Set(D.standard.map(r => r.config))].forEach(c => { const tr = el('tr'); tr.append(el('td', null, c));
    pols.forEach(p => { const r = D.standard.find(x => x.config === c && x.policy === p);
      tr.append(el('td', 'num', r.reward.toFixed(3) + ' / ' + r.f1.toFixed(2))); }); t.append(tr); });
  const z = $('#zooTable'), zh = el('tr');
  ['атака', 'с ошибками', 'чистые', 'правило', 'итог'].forEach((x, i) => zh.append(el('th', i === 1 || i === 2 ? 'num' : null, x))); z.append(zh);
  const ref = (n, v) => { const tr = el('tr'); tr.append(el('td', null, n), el('td', 'num', v[0].toFixed(3)), el('td', 'num', v[1].toFixed(3)), el('td', null, 'эталон'), el('td')); z.append(tr); };
  ref('честная сверка', D.zoo.honest); ref('молчание', D.zoo.silence);
  D.zoo.rows.forEach(r => { const tr = el('tr');
    tr.append(el('td', null, r.attack), el('td', 'num', r.dirty.toFixed(3)), el('td', 'num', r.clean.toFixed(3)), el('td', null, r.rule === '= honest' ? 'ровно как у честной сверки' : 'ниже честной; на чистых не выше молчания'), el('td', r.ok ? 'ok' : 'no', r.ok ? 'отбита' : 'прошла'));
    z.append(tr); });
}

function mode(levels) {
  $('#levelsView').hidden = !levels; $('#stdView').hidden = levels;
  $('#mLevels').setAttribute('aria-pressed', levels); $('#mStd').setAttribute('aria-pressed', !levels);
}
$('#mLevels').onclick = () => mode(true); $('#mStd').onclick = () => mode(false);
$('#foot').textContent = 'Версия генератора эпизодов ' + D.version + '. Все данные синтетические. Страница показывает прогоны скриптов на открытых seed и ничего не обучает.';
draw(); std();
</script></body></html>
"""


def build(path="viewer.html", seeds=20):
    data = json.dumps(collect(seeds), ensure_ascii=False).replace("</", "<\\/")
    Path(path).write_text(TEMPLATE.replace("__DATA__", data), encoding="utf-8")
    return path


def main(argv=None):
    out = (argv or sys.argv[1:] or ["viewer.html"])[0]
    print("wrote", build(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
