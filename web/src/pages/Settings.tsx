import { useCallback, useEffect, useState } from 'react'
import { api, RequestError } from '../api'
import { Alert, Badge, Spinner } from '../components/ui'
import BindDialog from '../components/BindDialog'
import type { AccountStatus, Balance, Health, SearchStatus } from '../types'

export default function Settings({ health, onChanged }: {
  health: Health | null
  onChanged?: () => void
}) {
  const [acc, setAcc] = useState<AccountStatus | null>(null)
  const [search, setSearch] = useState<SearchStatus | null>(null)
  const [balance, setBalance] = useState<Balance | null>(null)
  const [binding, setBinding] = useState(false)
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')

  const load = useCallback(() => {
    api.account().then(a => {
      setAcc(a)
      if (a.bound) api.balance().then(setBalance).catch(() => setBalance(null))
      else setBalance(null)
    }).catch((e: RequestError) => setErr(e.message))
    api.searchStatus().then(setSearch).catch(() => setSearch(null))
  }, [])

  useEffect(load, [load])

  const after = (m: string) => { setMsg(m); load(); onChanged?.(); setTimeout(() => setMsg(''), 5000) }

  const unbind = async (provider?: string) => {
    setBusy('unbind')
    try {
      const who = acc?.bindings.find(b => b.provider === provider)?.name_zh ?? ''
      await api.unbind(provider)
      after(`已解绑 ${who}，凭据已从系统凭据库删除`)
    } catch (e) { setErr((e as RequestError).message) } finally { setBusy('') }
  }

  // 切换生效账号。**不动任何凭据**——切回来还是原来那把 key，
  // 不用再去控制台复制一遍。
  const activate = async (provider: string) => {
    setBusy('activate')
    try {
      await api.activate(provider)
      const who = acc?.bindings.find(b => b.provider === provider)?.name_zh ?? provider
      after(`已切换到 ${who}`)
    } catch (e) { setErr((e as RequestError).message) } finally { setBusy('') }
  }

  const pickModel = async (model: string) => {
    setBusy('model')
    try { await api.setModel(model); after(`主模型已切换为 ${model}`) }
    catch (e) { setErr((e as RequestError).message) } finally { setBusy('') }
  }

  if (!acc) return err ? <Alert tone="err" title="读不到账号状态">{err}</Alert> : <Spinner />

  return (
    <>
      <div className="text-[15px] font-semibold mb-4">设置</div>
      {msg && <div className="mb-4"><Alert tone="info" title="已完成">{msg}</Alert></div>}
      {err && <div className="mb-4"><Alert tone="err" title="出错了">{err}</Alert></div>}

      <div className="grid md:grid-cols-2 gap-4">
        {/* ── 账号 ── */}
        <div className="card p-5">
          <div className="flex items-center justify-between mb-3 gap-2">
            <div className="text-[13px] font-medium">
              {acc.bound && acc.provider ? `${acc.provider.name_zh} 账号` : "AI 账号"}
            </div>
            <Badge kind={acc.bound ? 'ok' : 'warn'}>{acc.bound ? '已绑定' : '未绑定'}</Badge>
          </div>

          <Alert tone="info" title="本软件不自带 API key">{acc.notice}</Alert>

          {!acc.keyring_available && (
            <div className="mt-3">
              <Alert tone="err" title="系统凭据库不可用">
                找不到 keyring，无法安全保存 key。在装好之前绑定功能不可用——
                把 key 写成明文文件不是可接受的替代方案。
              </Alert>
            </div>
          )}

          {acc.bound && acc.binding ? (
            <div className="mt-4 space-y-3 text-[13px]">
              {acc.bindings.length > 1 && (
                <div>
                  <div className="text-[12px] mb-1.5" style={{ color: 'var(--sub)' }}>
                    已绑定 {acc.bindings.length} 个账号，点一下切换（<b>切换不动凭据</b>）
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {acc.bindings.map(b => (
                      <button key={b.provider}
                              className={'btn' + (b.active ? ' btn-p' : '')}
                              disabled={busy === 'activate' || b.active}
                              onClick={() => void activate(b.provider)}>
                        {b.name_zh}
                        {!b.key_present && <span className="badge b-err ml-1.5">凭据丢失</span>}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              <Row label="当前服务商">
                <span>{acc.provider?.name_zh ?? acc.binding.provider}</span>
              </Row>
              <Row label="API Key"><span className="num">{acc.binding.label}</span></Row>
              <Row label="服务地址"><span className="num text-[12px]">{acc.binding.base_url}</span></Row>
              <Row label="绑定时间">
                <span className="num text-[12px]">{acc.binding.bound_at.slice(0, 16).replace('T', ' ')}</span>
              </Row>
              <div>
                <div className="text-[12px] mb-1.5" style={{ color: 'var(--sub)' }}>
                  {acc.provider?.may_list_models
                    ? '主模型（清单来自该账号的 /models 接口，非写死）'
                    : '主模型（这家没有模型列表接口，需要从控制台复制模型名）'}
                </div>
                {acc.provider?.may_list_models ? (
                  <select className="inp" value={acc.binding.model} disabled={busy === 'model'}
                          onChange={e => void pickModel(e.target.value)}>
                    {acc.binding.models_seen.map(m => <option key={m} value={m}>{m}</option>)}
                  </select>
                ) : (
                  <input className="inp num" defaultValue={acc.binding.model}
                         placeholder={acc.provider?.model_hint}
                         disabled={busy === 'model'} spellCheck={false}
                         onBlur={e => {
                           const v = e.target.value.trim()
                           if (v && v !== acc.binding?.model) void pickModel(v)
                         }} />
                )}
              </div>

              {balance && (
                <div className="card2 p-3">
                  <div className="text-[12px] mb-1" style={{ color: 'var(--sub)' }}>账号余额</div>
                  {balance.available ? (
                    <div className="num text-[13px]">
                      {balance.total_balance} {balance.currency}
                      {balance.is_available === false && (
                        <span className="badge b-err ml-2">余额不足</span>
                      )}
                    </div>
                  ) : <div className="text-[12px]" style={{ color: 'var(--sub)' }}>{balance.note}</div>}
                </div>
              )}

              <div className="flex flex-wrap gap-2 pt-2">
                <button className="btn" disabled={busy === 'unbind'}
                        onClick={() => void unbind(acc.binding?.provider)}>
                  {busy === 'unbind' ? '解绑中…' : '解绑当前账号'}
                </button>
                <button className="btn" onClick={() => setBinding(true)}>换一个 key</button>
                <button className="btn" onClick={() => setBinding(true)}>再绑一家 +</button>
              </div>
            </div>
          ) : (
            <>
              <div className="mt-3 text-[12px] space-y-1.5" style={{ color: 'var(--sub)' }}>
                <div>
                  可绑定：{acc.providers.map(p => p.name_zh).join(' / ')}。
                </div>
                <div>① 选服务商，到它的控制台创建一个 API Key</div>
                <div>② 粘回软件验证。有模型列表接口的不消耗 token；
                  没有的（方舟/百炼）会发一次最小对话，<b>消耗几个 token</b>，绑定时会明说</div>
                <div>③ key 存进系统凭据库，不落明文文件、不入数据库、不随项目导出</div>
              </div>
              <button className="btn btn-p w-full mt-4" disabled={!acc.keyring_available}
                      onClick={() => setBinding(true)}>
                绑定账号 →
              </button>
            </>
          )}
        </div>

        {/* ── 用量与上限 ── */}
        <div className="card p-5">
          <div className="text-[13px] font-medium mb-3">用量与上限</div>
          <LimitsEditor limits={acc.limits} usage={acc.usage_today} onSaved={after} />
          <div className="text-[11px] mt-4 pt-3 border-t" style={{ borderColor: 'var(--line)', color: 'var(--sub)' }}>
            这里只显示真实的 token 数与真实余额，<b style={{ color: 'var(--txt)' }}>刻意不做费用换算</b>——
            单价会变，把价目表硬编码进来早晚会给出一个过期的"预估费用"。
            这个产品的立身之本就是不给来路不明的数字。
          </div>
        </div>

        {/* ── 搜索服务（引导式阶段 1 的检索供能）── */}
        <div className="card p-5">
          <div className="flex items-center justify-between mb-3 gap-2">
            <div className="text-[13px] font-medium">搜索服务</div>
            <Badge kind={search?.bound ? 'ok' : 'warn'}>
              {search?.bound ? '已绑定' : '未绑定'}
            </Badge>
          </div>
          {search ? (
            <SearchCard s={search} onChanged={after} onError={setErr} />
          ) : <Spinner label="正在读取搜索服务状态…" />}
        </div>

        {/* ── 引擎与数据 ── */}
        <div className="card p-5">
          <div className="text-[13px] font-medium mb-3">引擎与数据</div>
          <div className="space-y-3 text-[13px]">
            <Row label="引擎版本">{health ? `mds ${health.engine_version}` : '—'}</Row>
            <Row label="可执行工作流">{health ? `${health.workflows} 个` : '—'}</Row>
            <Row label="物料总数">{health ? `${health.materials} 个` : '—'}</Row>
            <Row label="网络可达">{health?.online ? '是' : '否'}</Row>
            <Row label="当前有效模式">
              <span className={`badge ${acc.effective_mode === 'online' ? 'b-ok' : 'b-warn'}`}>
                {acc.effective_mode === 'online' ? '在线（AI 可用）' : '离线内核'}
              </span>
            </Row>
            <div>
              <div className="text-[12px] mb-1" style={{ color: 'var(--sub)' }}>数据根目录</div>
              <div className="num text-[11px] break-all card2 p-2">{health?.skill_root ?? '—'}</div>
            </div>
          </div>
        </div>

        {/* ── 离线能力 ── */}
        <div className="card p-5">
          <div className="text-[13px] font-medium mb-3">离线能力</div>
          <div className="text-[12px] space-y-2" style={{ color: 'var(--sub)' }}>
            <p>以下功能不需要网络、也不需要绑定账号，始终可用：</p>
            <div className="card2 p-2">物料识别（关键词匹配）· 依据检索 · 参数校验</div>
            <div className="card2 p-2">分步计算 · 校核 · 结果表 · 信源清单</div>
            <div className="card2 p-2">采购关键词与链接生成 · 知识库自检与补录</div>
            <p className="pt-1">
              绑定账号增强四件事：把整句工况解析得更准、为缺失参数给建议值与理由、
              把某一步用白话讲清楚，以及<b style={{ color: 'var(--txt)' }}>引导式选型</b>
              （知识库里没有的物料，联网检索并取证依据后一步步引导你选完）。
              这四件事<b style={{ color: 'var(--txt)' }}>都不参与数值计算</b>——
              AI 给的参数会走与手工输入完全相同的校验通道，
              引导出来的公式里也不许出现编造的系数。
            </p>
            <p>
              引导式是唯一没有离线降级方案的一项：阶段 1 要真的去抓页面核对标准号，
              离线做不到。但引导出来的物料<b style={{ color: 'var(--txt)' }}>保存之后离线照常可选</b>。
            </p>
          </div>
        </div>
      </div>

      {binding && (
        <BindDialog providers={acc.providers} initial={acc.active}
                    onClose={() => setBinding(false)}
                    onDone={m => { setBinding(false); after(m) }} />
      )}
    </>
  )
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-2 items-center">
      <span style={{ color: 'var(--sub)' }}>{label}</span>
      <span className="text-right">{children}</span>
    </div>
  )
}

function LimitsEditor({ limits, usage, onSaved }: {
  limits: import('../types').AiLimits
  usage: import('../types').UsageToday
  onSaved: (m: string) => void
}) {
  const [form, setForm] = useState(limits)
  const [busy, setBusy] = useState(false)
  const dirty = JSON.stringify(form) !== JSON.stringify(limits)

  const save = async () => {
    setBusy(true)
    try { await api.setLimits(form); onSaved('调用上限已更新') }
    finally { setBusy(false) }
  }

  const pct = limits.max_calls_per_day
    ? Math.min(100, Math.round((usage.calls / limits.max_calls_per_day) * 100)) : 0

  return (
    <div className="space-y-3 text-[13px]">
      <div className="card2 p-3">
        <div className="flex justify-between text-[12px] mb-1.5">
          <span style={{ color: 'var(--sub)' }}>今日调用</span>
          <span className="num">{usage.calls} / {limits.max_calls_per_day} 次</span>
        </div>
        <div className="h-1.5 rounded-full overflow-hidden" style={{ background: 'var(--line)' }}>
          <div className="h-full rounded-full transition-all"
               style={{ width: `${pct}%`, background: pct > 80 ? 'var(--warn)' : 'var(--brand)' }} />
        </div>
        <div className="flex justify-between text-[11px] mt-2" style={{ color: 'var(--sub)' }}>
          <span>今日 token</span>
          <span className="num">
            {usage.total_tokens.toLocaleString()}
            {usage.cache_hit_tokens > 0 && `（缓存命中 ${usage.cache_hit_tokens.toLocaleString()}）`}
          </span>
        </div>
      </div>

      <NumField label="单次调用 token 上限" value={form.max_tokens_per_call}
                onChange={v => setForm({ ...form, max_tokens_per_call: v })} />
      <NumField label="每日调用次数上限" value={form.max_calls_per_day}
                onChange={v => setForm({ ...form, max_calls_per_day: v })} />
      <label className="flex items-center gap-2 text-[12px]">
        <input type="checkbox" checked={form.enabled}
               onChange={e => setForm({ ...form, enabled: e.target.checked })} />
        启用上限（超限直接拒绝调用，而不是静默继续）
      </label>
      <button className="btn w-full" disabled={!dirty || busy} onClick={() => void save()}>
        {busy ? '保存中…' : dirty ? '保存上限' : '已保存'}
      </button>
    </div>
  )
}

function NumField({ label, value, onChange }: {
  label: string; value: number; onChange: (v: number) => void
}) {
  return (
    <div>
      <div className="text-[12px] mb-1" style={{ color: 'var(--sub)' }}>{label}</div>
      <input className="inp num" value={value} inputMode="numeric"
             onChange={e => onChange(Number(e.target.value) || 0)} />
    </div>
  )
}


/**
 * 搜索服务的绑定 —— 引导式选型阶段 1 要真的联网检索依据。
 *
 * 与 AI 绑定**完全独立**：解绑搜索服务不动 AI 账号。没绑也能用，
 * 那时只在白名单站点的本地目录里找（冷门物料可能找不到），如实显示这一点。
 */
function SearchCard({ s, onChanged, onError }: {
  s: SearchStatus
  onChanged: (m: string) => void
  onError: (m: string) => void
}) {
  const [pick, setPick] = useState(s.providers[0]?.id ?? '')
  const [key, setKey] = useState('')
  const [busy, setBusy] = useState('')
  const spec = s.providers.find(p => p.id === pick)

  const bind = async () => {
    setBusy('bind')
    try {
      const out = await api.bindSearch(key.trim(), pick)
      setKey('')
      onChanged(`已绑定 ${out.name_zh}，验证时搜到 ${out.binding.last_hits} 条结果`)
    } catch (e) { onError((e as RequestError).message) } finally { setBusy('') }
  }

  const drop = async (provider: string) => {
    setBusy('unbind')
    try {
      await api.unbindSearch(provider)
      onChanged('已解绑搜索服务，凭据已从系统凭据库删除。引导式会退到白名单站点那一级')
    } catch (e) { onError((e as RequestError).message) } finally { setBusy('') }
  }

  return (
    <>
      <Alert tone="info" title="本软件同样不自带搜索 key">{s.notice}</Alert>

      <div className="mt-3 text-[12px]">
        <Row label="阶段 1 实际走哪一级">
          <span className={`badge ${s.rung === 'binding' ? 'b-ok' : 'b-warn'}`}>
            {s.rung === 'binding' ? '你绑定的搜索服务' : '白名单站内目录'}
          </span>
        </Row>
      </div>

      {s.bindings.length > 0 && (
        <div className="mt-3 space-y-2 text-[13px]">
          {s.bindings.map(b => (
            <div key={b.provider} className="card2 p-2 flex items-center justify-between gap-2">
              <span>
                {b.name_zh}　<span className="num text-[11px]">{b.label}</span>
                {!b.key_present && <span className="badge b-err ml-1.5">凭据丢失</span>}
              </span>
              <button className="btn text-[12px] py-1 px-2" disabled={busy === 'unbind'}
                      onClick={() => void drop(b.provider)}>解绑</button>
            </div>
          ))}
        </div>
      )}

      <div className="mt-4 space-y-2">
        <div className="text-[12px]" style={{ color: 'var(--sub)' }}>绑定一家（三家都有免费额度）</div>
        <div className="flex flex-wrap gap-2">
          {s.providers.map(p => (
            <button key={p.id} className={'btn text-[12px] py-1 px-2' + (pick === p.id ? ' btn-p' : '')}
                    onClick={() => setPick(p.id)}>{p.name_zh}</button>
          ))}
        </div>
        {spec && (
          <div className="text-[11px] card2 p-2" style={{ color: 'var(--sub)' }}>
            {spec.notes}
            <div className="mt-1">
              到{' '}
              <a href={spec.console_url} target="_blank" rel="noreferrer"
                 className="underline" style={{ color: '#60a5fa' }}>{spec.console_url}</a>{' '}
              创建 key（环境变量名通常是 <span className="num">{spec.key_env_hint}</span>）。
              <b>绑定验证会真的发一次查询，消耗一次配额</b>——三家都没有免费的验证接口。
            </div>
          </div>
        )}
        <input className="inp num" type="password" value={key} placeholder="粘贴搜索服务的 API Key"
               onChange={e => setKey(e.target.value)} />
        <button className="btn btn-p w-full" disabled={!key.trim() || !!busy}
                onClick={() => void bind()}>
          {busy === 'bind' ? '验证中…' : '绑定并验证 →'}
        </button>
      </div>

      <div className="mt-4 pt-3 border-t" style={{ borderColor: 'var(--line)' }}>
        <div className="text-[12px] mb-2" style={{ color: 'var(--sub)' }}>
          白名单站点（不绑搜索服务时就在这些站的本地目录里找）
        </div>
        {s.whitelist.map(w => (
          <div key={w.domain} className="text-[11px] mb-1.5" style={{ color: 'var(--sub)' }}>
            <span className="num">{w.domain}</span>{' '}
            <Badge kind={w.tier === 'trusted' ? 'ok' : 'info'}>
              {w.tier === 'trusted' ? '可信' : '普通'}
            </Badge>{' '}
            {w.name_zh}
          </div>
        ))}
        <div className="text-[11px] mt-2" style={{ color: 'var(--sub)' }}>
          标「可信」的站点，单独一处就够支撑一条依据往下走；
          但<b style={{ color: 'var(--txt)' }}>仍然不会自动标绿</b>——
          绿灯只给两个独立信源相互印证。
        </div>
      </div>
    </>
  )
}
