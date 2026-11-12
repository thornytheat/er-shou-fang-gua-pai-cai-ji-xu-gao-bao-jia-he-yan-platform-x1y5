import { useEffect, useMemo, useState, useSyncExternalStore } from 'react';
import { versionsApi } from '../api/versions';
import { useToast } from '../hooks/useToast';
import { authStore } from '../stores/authStore';
import { can } from '../constants/roles';
import { FLAG_TYPE_LABELS, REVIEW_LABELS, VERSION_STATUS_LABELS } from '../constants/enums';
import { confidence, dateText, pct, yuan } from '../utils/format';
import { errorMessage } from '../utils/errorMessage';
import EmptyState from '../components/common/EmptyState';
import ErrorState from '../components/common/ErrorState';
import LedgerTable from '../components/common/LedgerTable';
import MetricStrip from '../components/common/MetricStrip';
import StampTag from '../components/common/StampTag';
import type {
  MarketCompareResult,
  MarketDraftSummary,
  MarketVersionDetail,
  MarketVersionSummary,
} from '../types/models';

const RULE_LABELS: Record<string, string> = {
  sim_threshold: '去重连边阈值（相似度 ≥）',
  sim_weights: '相似度权重（面积/楼层/标题/单价）',
  area_tol_sqm: '面积容差（㎡）',
  floor_tol: '楼层容差（层）',
  price_tol_pct: '单价接近容差',
  min_samples: '最小样本量（套）',
  z_threshold: '稳健 z 阈值（|z| >）',
  dev_flag_pct: '判定偏离（% >）',
  dev_border_pct: '边界带下限（% >）',
  confidence_weights: '置信度权重（样本/z/完整度）',
  sample_full: '置信度满分样本量',
  z_full: '置信度满分 |z|',
};

function ruleText(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'object') {
    return Object.entries(value as Record<string, unknown>)
      .map(([key, item]) => `${key} ${item}`)
      .join(' · ');
  }
  return String(value);
}

function flagTotal(counts: Record<string, number> | undefined): number {
  return Object.values(counts || {}).reduce((sum, value) => sum + value, 0);
}

export default function Versions() {
  const auth = useSyncExternalStore(authStore.subscribe, authStore.getState);
  const role = auth.user?.role;
  const canPublish = can(role, 'market:publish');
  const canCsv = can(role, 'export:read');
  const toast = useToast();

  const [items, setItems] = useState<MarketVersionSummary[]>([]);
  const [draft, setDraft] = useState<MarketDraftSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState('');
  const [title, setTitle] = useState('');
  const [note, setNote] = useState('');
  const [selected, setSelected] = useState<MarketVersionDetail | null>(null);
  const [retracting, setRetracting] = useState<MarketVersionSummary | null>(null);
  const [retractNote, setRetractNote] = useState('');
  const [fromId, setFromId] = useState<number | ''>('');
  const [toId, setToId] = useState<number | ''>('');
  const [compare, setCompare] = useState<MarketCompareResult | null>(null);

  async function load() {
    setLoading(true);
    try {
      const data = await versionsApi.list();
      setItems(data.items);
      setDraft(data.draft);
      setError(null);
      if (data.items.length >= 2 && !fromId && !toId) {
        setFromId(data.items[1].id);
        setToId(data.items[0].id);
      }
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  const latestPublished = useMemo(
    () => items.find((item) => item.status === 'published') || null,
    [items],
  );

  async function publish() {
    setBusy('publish');
    try {
      const version = await versionsApi.publish({ title: title || undefined, note: note || undefined });
      toast.ok(`${version.version_no} 已发布：${version.row_count} 行行情已冻结，回读与本次导出一致`);
      setTitle('');
      setNote('');
      setSelected(version);
      await load();
    } catch (err) {
      toast.error(errorMessage((err as { payload?: unknown }).payload, '发布失败'));
    } finally {
      setBusy('');
    }
  }

  async function confirmRetract() {
    if (!retracting) return;
    setBusy('retract');
    try {
      const updated = await versionsApi.retract(retracting.id, retractNote || undefined);
      toast.ok(`${updated.version_no} 已撤回，快照保留可回读；当前生效回到上一版`);
      setRetracting(null);
      setRetractNote('');
      await load();
    } catch (err) {
      toast.error(errorMessage((err as { payload?: unknown }).payload, '撤回失败'));
    } finally {
      setBusy('');
    }
  }

  async function openDetail(id: number) {
    setBusy(`detail-${id}`);
    try {
      setSelected(await versionsApi.detail(id));
    } catch (err) {
      toast.error(errorMessage((err as { payload?: unknown }).payload, '版本没读出来'));
    } finally {
      setBusy('');
    }
  }

  async function openDraft() {
    setBusy('draft');
    try {
      setSelected(await versionsApi.draft());
    } catch (err) {
      toast.error(errorMessage((err as { payload?: unknown }).payload, '草稿没读出来'));
    } finally {
      setBusy('');
    }
  }

  async function runCompare(from: number, to: number) {
    setBusy('compare');
    try {
      setCompare(await versionsApi.compare(from, to));
    } catch (err) {
      toast.error(errorMessage((err as { payload?: unknown }).payload, '对比失败'));
    } finally {
      setBusy('');
    }
  }

  async function download(version: MarketVersionSummary) {
    setBusy(`csv-${version.id}`);
    try {
      const rows = await versionsApi.downloadCsv(version.version_no, version.id);
      toast.ok(`${version.version_no} CSV 已下载，共 ${rows} 行（与发布当时逐字节一致）`);
    } catch (err) {
      toast.error(errorMessage((err as { payload?: unknown }).payload, '下载失败'));
    } finally {
      setBusy('');
    }
  }

  const metrics = [
    { k: '待发布行情', v: draft ? draft.row_count : '—', unit: '行', d: draft ? `算于 ${dateText(draft.computed_at)}` : '仅内部角色可见' },
    { k: '待发布工单', v: draft ? flagTotal(draft.flag_counts) : '—', unit: '件', d: draft ? `虚高 ${draft.flag_counts.high ?? 0} / 虚低 ${draft.flag_counts.low ?? 0} / 待确认 ${draft.flag_counts.uncertain ?? 0}` : '' },
    { k: '已发布版本', v: items.filter((item) => item.status === 'published').length, unit: '版', d: `含撤回共 ${items.length} 版` },
    { k: '当前生效', v: latestPublished ? latestPublished.version_no : '—', d: latestPublished ? `${dateText(latestPublished.published_at)} 发布` : '还没有发布过' },
    {
      k: '待发布距上版',
      v: draft?.vs_latest ? `+${draft.vs_latest.rows_added} / -${draft.vs_latest.rows_removed} / 改 ${draft.vs_latest.rows_changed}` : '—',
      d: draft?.vs_latest ? `块变化 ${draft.vs_latest.blocks_changed} · 工单变化 ${draft.vs_latest.flags_added + draft.vs_latest.flags_removed + draft.vs_latest.flags_changed}` : '样本变化后自动失效重算',
    },
  ];

  const versionColumns = [
    {
      key: 'version',
      label: '版本',
      render: (row: MarketVersionSummary) => (
        <div>
          <div style={{ fontWeight: 600 }}>
            <span className="mono">{row.version_no}</span>
            {row.title ? <span style={{ marginLeft: 6 }}>{row.title}</span> : null}
          </div>
          <div className="tiny muted">{row.note || '—'}</div>
        </div>
      ),
    },
    {
      key: 'status',
      label: '状态',
      render: (row: MarketVersionSummary) => (
        <div>
          <StampTag kind={row.status} label={VERSION_STATUS_LABELS[row.status] || row.status} />
          {row.status === 'retracted' && row.retracted_by ? (
            <div className="tiny muted" style={{ marginTop: 3 }}>
              {row.retracted_by} · {dateText(row.retracted_at)}
            </div>
          ) : null}
        </div>
      ),
    },
    {
      key: 'published',
      label: '发布',
      render: (row: MarketVersionSummary) => (
        <div>
          <div className="tiny">{dateText(row.published_at)}</div>
          <div className="tiny muted">{row.published_by}</div>
        </div>
      ),
    },
    {
      key: 'rows',
      label: '行情',
      align: 'right' as const,
      render: (row: MarketVersionSummary) => (
        <div>
          <div className="mono">{row.row_count} 行</div>
          <div className="tiny muted">工单 {flagTotal(row.flag_counts)}</div>
        </div>
      ),
    },
    {
      key: 'hash',
      label: 'CSV 指纹',
      align: 'right' as const,
      render: (row: MarketVersionSummary) => <span className="mono tiny" title={row.csv_hash}>{row.csv_hash.slice(0, 12)}</span>,
    },
    {
      key: 'actions',
      label: '',
      align: 'right' as const,
      render: (row: MarketVersionSummary) => (
        <div className="row" style={{ gap: 4, justifyContent: 'flex-end' }}>
          <button className="btn btn-sm btn-ghost" disabled={busy === `detail-${row.id}`} onClick={() => openDetail(row.id)}>
            详情
          </button>
          {canCsv ? (
            <button className="btn btn-sm btn-ghost" disabled={busy === `csv-${row.id}`} onClick={() => download(row)}>
              CSV
            </button>
          ) : null}
          {canPublish && row.status === 'published' && latestPublished?.id === row.id ? (
            <button
              className="btn btn-sm"
              onClick={() => {
                setRetracting(row);
                setRetractNote('');
              }}
              data-testid="retract-version"
            >
              撤回
            </button>
          ) : null}
        </div>
      ),
    },
  ];

  const snapshot = selected?.snapshot || null;

  return (
    <div className="stack fade-in">
      <MetricStrip items={metrics} />

      {draft ? (
        <div className="panel">
          <div className="panel-head">
            <h2>待发布（草稿）</h2>
            <div className="row" style={{ gap: 6 }}>
              <button className="btn btn-sm btn-ghost" disabled={busy === 'draft'} onClick={openDraft}>
                展开草稿快照
              </button>
              <button
                className="btn btn-primary btn-sm"
                disabled={!canPublish || busy === 'publish'}
                onClick={publish}
                title={canPublish ? '把当下行情冻结成新版本' : '只有研究组长能发布（服务端 403）'}
                data-testid="publish-version"
              >
                {busy === 'publish' ? '发布中…' : '发布这一版'}
              </button>
            </div>
          </div>
          <div className="panel-body stack" style={{ gap: 10 }}>
            <div className="row" style={{ gap: 18, flexWrap: 'wrap' }}>
              <div>
                <div className="tiny muted">行情行数</div>
                <div className="mono">{draft.row_count}</div>
              </div>
              <div>
                <div className="tiny muted">工单</div>
                <div className="mono">{flagTotal(draft.flag_counts)}</div>
              </div>
              <div>
                <div className="tiny muted">内容指纹</div>
                <div className="mono tiny" title={draft.input_hash}>{draft.input_hash.slice(0, 12)}</div>
              </div>
              <div>
                <div className="tiny muted">重算时间</div>
                <div className="mono tiny">{dateText(draft.computed_at)}</div>
              </div>
              {draft.vs_latest ? (
                <div>
                  <div className="tiny muted">距 {draft.latest_version_no}</div>
                  <div className="mono tiny">
                    +{draft.vs_latest.rows_added} / -{draft.vs_latest.rows_removed} / 改 {draft.vs_latest.rows_changed} 行
                  </div>
                </div>
              ) : null}
            </div>
            <div className="tiny muted">
              采集、改价、复核或重算之后，待发布结果会自动失效并按最新样本重算；已发布版本保持原样，不受后续改写影响。
            </div>
            {canPublish ? (
              <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
                <input
                  className="input"
                  style={{ width: 180 }}
                  placeholder="版本标题（可空）"
                  value={title}
                  onChange={(event) => setTitle(event.target.value)}
                />
                <input
                  className="input"
                  style={{ flex: 1, minWidth: 220 }}
                  placeholder="发布备注（写进版本与审计，可空）"
                  value={note}
                  onChange={(event) => setNote(event.target.value)}
                />
              </div>
            ) : (
              <div className="tiny muted">数据专员可以看草稿口径，发布与撤回由研究组长操作。</div>
            )}
          </div>
        </div>
      ) : null}

      <div className="panel">
        <div className="panel-head">
          <h2>发布历史</h2>
          <span className="tiny muted">发布即冻结主房源 / 可比样本 / 异常工单 / 判价依据，任何时间回读与当时导出一致</span>
        </div>
        <div className="panel-body stack" style={{ gap: 12 }}>
          {error ? <ErrorState error={error} onRetry={load} title="发布历史没读出来" /> : null}
          {!error && loading && items.length === 0 ? (
            <div className="empty"><span className="spinner" /> 正在读发布历史 …</div>
          ) : null}
          {!error && !loading && items.length === 0 ? (
            <EmptyState title="还没有发布过行情版本" hint="待发布草稿确认无误后，点「发布这一版」冻结成 V0001。" />
          ) : null}
          {items.length > 0 ? <LedgerTable columns={versionColumns} rows={items} rowKey={(row) => row.id} testId="version-ledger" /> : null}

          {retracting ? (
            <div className="panel" style={{ background: 'var(--paper-2)' }}>
              <div className="panel-body stack" style={{ gap: 8 }}>
                <div>
                  撤回 <b className="mono">{retracting.version_no}</b>：已发数字保留快照可回读，但当前生效口径回到上一版。此动作写审计。
                </div>
                <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
                  <input
                    className="input"
                    style={{ flex: 1, minWidth: 220 }}
                    placeholder="撤回原因（写进版本与审计）"
                    value={retractNote}
                    onChange={(event) => setRetractNote(event.target.value)}
                  />
                  <button className="btn btn-sm btn-primary" disabled={busy === 'retract'} onClick={confirmRetract}>
                    {busy === 'retract' ? '撤回中…' : '确认撤回'}
                  </button>
                  <button className="btn btn-sm btn-ghost" onClick={() => setRetracting(null)}>
                    取消
                  </button>
                </div>
              </div>
            </div>
          ) : null}
        </div>
      </div>

      {items.length >= 2 ? (
        <div className="panel">
          <div className="panel-head">
            <h2>两版对比</h2>
            <div className="row" style={{ gap: 6 }}>
              <select className="select" value={fromId} onChange={(event) => setFromId(Number(event.target.value))}>
                {items.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.version_no}（{VERSION_STATUS_LABELS[item.status] || item.status}）
                  </option>
                ))}
              </select>
              <span className="tiny muted">→</span>
              <select className="select" value={toId} onChange={(event) => setToId(Number(event.target.value))}>
                {items.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.version_no}（{VERSION_STATUS_LABELS[item.status] || item.status}）
                  </option>
                ))}
              </select>
              <button
                className="btn btn-sm"
                disabled={!fromId || !toId || fromId === toId || busy === 'compare'}
                onClick={() => runCompare(Number(fromId), Number(toId))}
                data-testid="compare-versions"
              >
                {busy === 'compare' ? '对比中…' : '对比'}
              </button>
            </div>
          </div>
          <div className="panel-body stack" style={{ gap: 12 }}>
            {compare ? (
              <>
                <div className="row" style={{ gap: 18, flexWrap: 'wrap' }}>
                  <div>
                    <div className="tiny muted">新增行</div>
                    <div className="mono">{compare.summary.rows_added}</div>
                  </div>
                  <div>
                    <div className="tiny muted">移除行</div>
                    <div className="mono">{compare.summary.rows_removed}</div>
                  </div>
                  <div>
                    <div className="tiny muted">字段变动</div>
                    <div className="mono">{compare.summary.rows_changed}</div>
                  </div>
                  <div>
                    <div className="tiny muted">样本块变化</div>
                    <div className="mono">{compare.summary.blocks_changed}</div>
                  </div>
                  <div>
                    <div className="tiny muted">工单变化</div>
                    <div className="mono">
                      +{compare.summary.flags_added} / -{compare.summary.flags_removed} / 改 {compare.summary.flags_changed}
                    </div>
                  </div>
                </div>

                {compare.rows_changed.items.length ? (
                  <div>
                    <div className="eyebrow" style={{ marginBottom: 4 }}>字段变动（{compare.rows_changed.total}{compare.rows_changed.truncated ? '，仅列前 100' : ''}）</div>
                    <div className="ledger-wrap">
                      <table className="ledger">
                        <thead>
                          <tr>
                            <th>房源</th>
                            <th>字段</th>
                            <th className="right">{compare.from.version_no}</th>
                            <th className="right">{compare.to.version_no}</th>
                          </tr>
                        </thead>
                        <tbody>
                          {compare.rows_changed.items.flatMap((row) =>
                            row.changes.map((change, index) => (
                              <tr key={`${row.listing_id}-${change.field}-${index}`}>
                                {index === 0 ? <td rowSpan={row.changes.length}>{row.label}</td> : null}
                                <td className="tiny">{change.field}</td>
                                <td className="num right muted">{String(change.from) || '—'}</td>
                                <td className="num right">{String(change.to) || '—'}</td>
                              </tr>
                            )),
                          )}
                        </tbody>
                      </table>
                    </div>
                  </div>
                ) : null}

                {compare.rows_added.items.length || compare.rows_removed.items.length ? (
                  <div className="row" style={{ gap: 18, alignItems: 'flex-start', flexWrap: 'wrap' }}>
                    {compare.rows_added.items.length ? (
                      <div style={{ flex: 1, minWidth: 220 }}>
                        <div className="eyebrow" style={{ marginBottom: 4 }}>新增（{compare.rows_added.total}）</div>
                        {compare.rows_added.items.map((row) => (
                          <div className="tiny" key={row.listing_id}>＋ {row.label}</div>
                        ))}
                      </div>
                    ) : null}
                    {compare.rows_removed.items.length ? (
                      <div style={{ flex: 1, minWidth: 220 }}>
                        <div className="eyebrow" style={{ marginBottom: 4 }}>移除（{compare.rows_removed.total}）</div>
                        {compare.rows_removed.items.map((row) => (
                          <div className="tiny muted" key={row.listing_id}>－ {row.label}</div>
                        ))}
                      </div>
                    ) : null}
                  </div>
                ) : null}

                {compare.blocks_changed.items.length ? (
                  <div>
                    <div className="eyebrow" style={{ marginBottom: 4 }}>可比样本块（{compare.blocks_changed.total}）</div>
                    <div className="ledger-wrap">
                      <table className="ledger">
                        <thead>
                          <tr>
                            <th>小区 · 户型</th>
                            <th>指标</th>
                            <th className="right">{compare.from.version_no}</th>
                            <th className="right">{compare.to.version_no}</th>
                          </tr>
                        </thead>
                        <tbody>
                          {compare.blocks_changed.items.flatMap((block) =>
                            Object.entries(block.changes).map(([field, [before, after]], index) => (
                              <tr key={`${block.block_key}-${field}`}>
                                {index === 0 ? <td rowSpan={Object.keys(block.changes).length}>{block.community} · {block.layout}</td> : null}
                                <td className="tiny">{field}</td>
                                <td className="num right muted">{before === null || before === undefined ? '—' : String(before)}</td>
                                <td className="num right">{after === null || after === undefined ? '—' : String(after)}</td>
                              </tr>
                            )),
                          )}
                        </tbody>
                      </table>
                    </div>
                  </div>
                ) : null}

                {compare.flags_added.items.length || compare.flags_removed.items.length || compare.flags_changed.items.length ? (
                  <div>
                    <div className="eyebrow" style={{ marginBottom: 4 }}>异常工单变化</div>
                    {compare.flags_changed.items.map((flag) => (
                      <div className="tiny" key={`c-${flag.listing_id}`}>
                        工单房源 #{flag.listing_id}：{FLAG_TYPE_LABELS[flag.from.flag_type] || flag.from.flag_type}·{REVIEW_LABELS[flag.from.review_status] || flag.from.review_status}
                        {' → '}
                        {FLAG_TYPE_LABELS[flag.to.flag_type] || flag.to.flag_type}·{REVIEW_LABELS[flag.to.review_status] || flag.to.review_status}
                      </div>
                    ))}
                    {compare.flags_added.items.map((flag) => (
                      <div className="tiny" key={`a-${flag.listing_id}`}>＋ 工单房源 #{flag.listing_id}（{FLAG_TYPE_LABELS[flag.flag_type] || flag.flag_type}）</div>
                    ))}
                    {compare.flags_removed.items.map((flag) => (
                      <div className="tiny muted" key={`r-${flag.listing_id}`}>－ 工单房源 #{flag.listing_id}（{FLAG_TYPE_LABELS[flag.flag_type] || flag.flag_type}）</div>
                    ))}
                  </div>
                ) : null}

                {!compare.summary.rows_added && !compare.summary.rows_removed && !compare.summary.rows_changed && !compare.summary.blocks_changed && !compare.summary.flags_added && !compare.summary.flags_removed && !compare.summary.flags_changed ? (
                  <EmptyState title="两版内容完全一致" hint="行、样本块与工单都没有差异。" />
                ) : null}
              </>
            ) : (
              <div className="tiny muted">选两个版本点「对比」：逐行字段、样本块中位/均价、异常工单三口径对照。</div>
            )}
          </div>
        </div>
      ) : null}

      {selected && snapshot ? (
        <div className="panel">
          <div className="panel-head">
            <h2>
              <span className="mono">{selected.version_no}</span> 快照
            </h2>
            <div className="row" style={{ gap: 6, alignItems: 'center' }}>
              <StampTag kind={selected.status} label={VERSION_STATUS_LABELS[selected.status] || selected.status} />
              <button className="btn btn-sm btn-ghost" onClick={() => setSelected(null)}>
                收起
              </button>
            </div>
          </div>
          <div className="panel-body stack" style={{ gap: 14 }}>
            <div className="row" style={{ gap: 18, flexWrap: 'wrap' }}>
              <div>
                <div className="tiny muted">快照生成</div>
                <div className="mono tiny">{dateText(snapshot.generated_at)}</div>
              </div>
              <div>
                <div className="tiny muted">内容指纹</div>
                <div className="mono tiny" title={selected.input_hash}>{selected.input_hash.slice(0, 12)}</div>
              </div>
              <div>
                <div className="tiny muted">CSV 指纹</div>
                <div className="mono tiny" title={selected.csv_hash}>{selected.csv_hash.slice(0, 12)}</div>
              </div>
              {selected.retracted_by ? (
                <div>
                  <div className="tiny muted">撤回</div>
                  <div className="tiny">{selected.retracted_by} · {dateText(selected.retracted_at)}{selected.retract_note ? ` · ${selected.retract_note}` : ''}</div>
                </div>
              ) : null}
            </div>

            <div>
              <div className="eyebrow" style={{ marginBottom: 4 }}>判价依据（发布时的算法口径）</div>
              <div className="ledger-wrap">
                <table className="ledger">
                  <tbody>
                    {Object.entries(snapshot.rules).map(([key, value]) => (
                      <tr key={key}>
                        <td className="tiny" style={{ width: 220 }}>{RULE_LABELS[key] || key}</td>
                        <td className="mono tiny">{ruleText(value)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            <div>
              <div className="eyebrow" style={{ marginBottom: 4 }}>可比样本（{snapshot.blocks.length} 个小区×户型块）</div>
              <div className="ledger-wrap">
                <table className="ledger">
                  <thead>
                    <tr>
                      <th>小区 · 户型</th>
                      <th className="right">样本</th>
                      <th className="right">中位</th>
                      <th className="right">MAD</th>
                      <th className="right">去重后均价</th>
                      <th className="right">去重前均价</th>
                      <th className="right">面积缺失</th>
                    </tr>
                  </thead>
                  <tbody>
                    {snapshot.blocks.map((block) => (
                      <tr key={block.block_key}>
                        <td>{block.community} · {block.layout}</td>
                        <td className="num right" title={`样本房源 id：${block.sample_ids.join(', ')}`}>{block.primary_count}</td>
                        <td className="num right">{block.median_price ? yuan(block.median_price) : '—'}</td>
                        <td className="num right">{block.mad ? yuan(block.mad) : '—'}</td>
                        <td className="num right" style={{ color: 'var(--ochre)' }}>{block.dedup_mean ? yuan(block.dedup_mean) : '—'}</td>
                        <td className="num right muted">{block.naive_mean ? yuan(block.naive_mean) : '—'}</td>
                        <td className="num right muted">{block.unknown_count || '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            <div>
              <div className="eyebrow" style={{ marginBottom: 4 }}>异常工单（{snapshot.flags.length} 件，含判价依据）</div>
              {snapshot.flags.length ? (
                <div className="ledger-wrap">
                  <table className="ledger">
                    <thead>
                      <tr>
                        <th>标记</th>
                        <th>房源</th>
                        <th className="right">单价 / 中位</th>
                        <th className="right">偏离</th>
                        <th className="right">z</th>
                        <th className="right">置信</th>
                        <th>依据</th>
                      </tr>
                    </thead>
                    <tbody>
                      {snapshot.flags.map((flag) => (
                        <tr key={flag.flag_id}>
                          <td>
                            <div className="row" style={{ gap: 4 }}>
                              <StampTag kind={flag.flag_type} />
                              <span className={`stamp ${flag.review_status}`}>{REVIEW_LABELS[flag.review_status] || flag.review_status}</span>
                            </div>
                          </td>
                          <td className="tiny">{flag.community} · {flag.layout} #{flag.listing_id}</td>
                          <td className="num right">
                            <div className="mono">{yuan(flag.unit_price)}</div>
                            <div className="tiny muted">中位 {yuan(flag.median_price)}</div>
                          </td>
                          <td className="num right mono">{pct(flag.deviation_pct)}</td>
                          <td className="num right mono">{flag.z_score !== null ? flag.z_score.toFixed(2) : '—'}</td>
                          <td className="num right mono">{confidence(flag.confidence)}</td>
                          <td className="tiny muted" style={{ maxWidth: 320 }}>{flag.basis}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="tiny muted">这一版没有异常工单。</div>
              )}
            </div>

            <div>
              <div className="eyebrow" style={{ marginBottom: 4 }}>行情明细（{snapshot.rows.length} 行冻结主房源，与 CSV 一致）</div>
              <div className="ledger-wrap">
                <table className="ledger" data-testid="version-snapshot-rows">
                  <thead>
                    <tr>
                      {snapshot.headers.map((header) => (
                        <th key={header}>{header}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {snapshot.rows.map((row, index) => (
                      <tr key={snapshot.row_keys[index]?.listing_id ?? index}>
                        {row.map((value, cell) => (
                          <td key={cell} className={typeof value === 'number' ? 'num' : 'tiny'}>
                            {value === '' ? '—' : value}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
