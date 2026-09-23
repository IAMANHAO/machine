import { useMemo, useState } from 'react'
import { api, RequestError } from '../api'
import type { ProviderSpec } from '../types'
import { Alert } from './ui'
import Modal from './Modal'

/**
 * 绑定对话框 —— 支持 DeepSeek / 火山方舟（豆包）/ 阿里百炼（千问）/ OpenAI，
 * 以及任何自填 base_url 的 OpenAI 兼容服务。
 *
 * 四家都只提供 API Key + Bearer 认证，**没有面向第三方应用的 OAuth 授权登录**，
 * 所以不可能做成"跳转登录"。做成引导式绑定：选服务商 → 说清费用归属 →
 * 打开其控制台 → 粘回 key → 验证。
 *
 * 两处必须诚实的地方：
 *
 * 1. **验证花不花钱要说在前面。** 有模型列表接口的（DeepSeek/OpenAI）走
 *    GET /models，不消耗 token；没有的（方舟/百炼）要发一次最小对话探针，
 *    那是花用户的钱。哪怕只有几个 token，也不能等花完了再说。
 * 2. **每家的坑要在选中时就显示**，而不是等绑定失败了让用户自己猜。
 *
 * key 只在提交的那一刻存在于这个组件里，提交后交给后端写入系统凭据库，
 * 前端不留存、不回显、不写 localStorage。
 */
export default function BindDialog({ providers, initial, onClose, onDone }: {
  providers: ProviderSpec[]
  initial?: string
  onClose: () => void
  onDone: (msg: string) => void
}) {
  const [pid, setPid] = useState(initial || providers[0]?.id || 'deepseek')
  const [key, setKey] = useState('')
  const [model, setModel] = useState('')
  const [baseUrl, setBaseUrl] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [opened, setOpened] = useState(false)

  const spec = useMemo(
    () => providers.find(p => p.id === pid) ?? providers[0],
    [providers, pid])

  // 自填服务没有内置 base_url，必须由用户给——否则会打到一个他没选过的地方去
  const needsBaseUrl = !!spec && !spec.base_url
  // 没有模型列表接口的，模型名必填：引擎没法替他列出来
  const needsModel = !!spec && !spec.may_list_models
  const ready = key.trim() && (!needsBaseUrl || baseUrl.trim()) &&
                (!needsModel || model.trim())

  const submit = async () => {
    setBusy(true); setError('')
    try {
      const res = await api.bind(key.trim(), pid, model.trim(), baseUrl.trim())
      setKey('')  // 立刻从内存里抹掉
      const cost = res.validation?.cost_hint ?? ''
      onDone(`已绑定 ${spec?.name_zh}（${res.binding.label}），模型 ${res.binding.model}。${cost}`)
    } catch (e) {
      setError((e as RequestError).message)
    } finally {
      setBusy(false)
    }
  }

  const host = spec?.console_url
    ? spec.console_url.replace(/^https?:\/\//, '').split('/')[0]
    : ''

  return (
    <Modal title="绑定 AI 账号" onClose={onClose} width={640}>
      <div className="mb-4">
        <Alert tone="warn" title="费用计入你自己的账号">
          本软件不自带 API key，也不自建中转服务、不代付任何费用。
          绑定后所有 AI 调用直接计入你在该平台的账号；
          请求由本机直连服务商，工况参数不经过任何第三方服务器。
        </Alert>
      </div>

      <ol className="space-y-4 text-[13px]">
        <li>
          <Step n={1} title="选择服务商" done={!!spec} />
          <div className="ml-7 mt-2 flex flex-wrap gap-2">
            {providers.map(p => (
              <button key={p.id}
                      className={'btn' + (p.id === pid ? ' btn-p' : '')}
                      onClick={() => { setPid(p.id); setModel(''); setBaseUrl(''); setError('') }}>
                {p.name_zh}
              </button>
            ))}
          </div>
          {spec?.notes && (
            <div className="ml-7 mt-2 text-[11px] leading-relaxed"
                 style={{ color: 'var(--sub)' }}>
              {spec.notes}
            </div>
          )}
        </li>

        <li>
          <Step n={2} title={`在${spec?.name_zh ?? ''}控制台创建 API Key`} />
          <div className="ml-7 mt-2">
            {spec?.console_url ? (
              <a className="btn inline-block" href={spec.console_url} target="_blank"
                 rel="noreferrer noopener" onClick={() => setOpened(true)}>
                打开 {host} ↗
              </a>
            ) : (
              <span className="text-[12px]" style={{ color: 'var(--sub)' }}>
                自填服务：请到你自己的服务商处获取 key。
              </span>
            )}
            <div className="text-[11px] mt-1.5" style={{ color: 'var(--sub)' }}>
              key 通常只显示一次，创建后请立刻复制。关掉页面就看不到了，丢了只能重新建一个。
              {spec?.key_env_hint &&
                <> 官方文档里把它叫做 <span className="num">{spec.key_env_hint}</span>。</>}
            </div>
          </div>
        </li>

        {needsBaseUrl && (
          <li>
            <Step n={3} title="填写 base_url" done={!!baseUrl.trim()} />
            <div className="ml-7 mt-2">
              <input className="inp num" value={baseUrl} spellCheck={false}
                     placeholder="https://your-service/v1"
                     onChange={e => setBaseUrl(e.target.value)} />
              <div className="text-[11px] mt-1.5" style={{ color: 'var(--sub)' }}>
                只要对方实现了 OpenAI 的 <span className="num">POST /chat/completions</span> 就能用。
              </div>
            </div>
          </li>
        )}

        <li>
          <Step n={needsBaseUrl ? 4 : 3} title="把 key 粘回这里" done={!!key.trim()} />
          <div className="ml-7 mt-2">
            <input className="inp num" type="password" value={key} autoComplete="off"
                   placeholder="sk-..." spellCheck={false}
                   onChange={e => setKey(e.target.value)}
                   onKeyDown={e => { if (e.key === 'Enter' && ready) void submit() }} />
            <div className="text-[11px] mt-1.5" style={{ color: 'var(--sub)' }}>
              提交后写入 Windows 凭据管理器，不落明文文件、不入数据库、不随项目导出。
              界面上今后只显示脱敏后的形式。
            </div>
          </div>
        </li>

        <li>
          <Step n={needsBaseUrl ? 5 : 4}
                title={needsModel ? '填写模型名（这家必填）' : '模型（可留空，绑定后自动取清单）'}
                done={!needsModel || !!model.trim()} />
          <div className="ml-7 mt-2">
            <input className="inp num" value={model} spellCheck={false}
                   placeholder={spec?.model_hint || 'gpt-4.1-mini'}
                   onChange={e => setModel(e.target.value)} />
            <div className="text-[11px] mt-1.5" style={{ color: 'var(--sub)' }}>
              {needsModel
                ? <>这家<b>没有提供模型列表接口</b>，模型名要从控制台复制。
                    占位里的 <span className="num">{spec?.model_hint}</span> 只是个格式示例，
                    <b>不保证现在还可用</b>——以控制台为准。</>
                : <>留空则取账号可用清单里的第一个，绑定后可在设置页切换。</>}
            </div>
          </div>
        </li>

        <li>
          <Step n={needsBaseUrl ? 6 : 5} title="验证可用性" />
          <div className="ml-7 mt-2 text-[12px]" style={{ color: 'var(--sub)' }}>
            {spec?.may_list_models
              ? <>先发一次 <span className="num">GET /models</span>：它同时证明 key 有效、
                  网络可达，并拿到你这个账号真实可用的模型清单（所以软件里不写死模型名）。
                  <b>这个请求不消耗 token。</b></>
              : <>这家没有免费的模型列表接口，验证会改发一次
                  <span className="num"> max_tokens=1 </span>的最小对话请求——
                  <b>会消耗几个 token，计入你自己的账号</b>。绑定完成后会告诉你具体消耗了多少。</>}
          </div>
        </li>
      </ol>

      {error && <div className="mt-4"><Alert tone="err" title="绑定失败">{error}</Alert></div>}
      {!opened && !key.trim() && spec?.console_url && (
        <div className="mt-4 text-[11px]" style={{ color: 'var(--sub)' }}>
          还没有 key？先点上面第二步的按钮去创建。
        </div>
      )}

      <div className="flex gap-2 pt-4 mt-4 border-t" style={{ borderColor: 'var(--line)' }}>
        <button className="btn" onClick={onClose}>取消</button>
        <div className="flex-1" />
        <button className="btn btn-p" disabled={!ready || busy}
                onClick={() => void submit()}>
          {busy ? '验证中…' : '验证并绑定'}
        </button>
      </div>
    </Modal>
  )
}

function Step({ n, title, done }: { n: number; title: string; done?: boolean }) {
  return (
    <div className="flex items-center gap-2.5">
      <span className="dot" style={done ? {
        background: 'rgba(16,185,129,.15)', borderColor: 'rgba(16,185,129,.5)', color: '#34d399',
      } : undefined}>{done ? '✓' : n}</span>
      <span className="font-medium">{title}</span>
    </div>
  )
}
