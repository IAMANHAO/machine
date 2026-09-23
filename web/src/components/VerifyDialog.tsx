import { useState } from 'react'
import { api, RequestError } from '../api'
import type { TableAudit } from '../types'
import { Alert, ConfidenceBadge } from './ui'
import Modal from './Modal'

/**
 * 双源核验对话框。
 *
 * 第二信源是必填的，且不能与主信源相同——这不是走形式：
 * 绿灯的意义在于"有两个独立信源相互印证且可追溯"，
 * 填不出第二信源就说明还不该给绿灯。
 */
export default function VerifyDialog({ table, onClose, onDone }: {
  table: TableAudit
  onClose: () => void
  onDone: (msg: string) => void
}) {
  const [second, setSecond] = useState('')
  const [by, setBy] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async () => {
    setBusy(true); setError('')
    try {
      await api.verifyTable(table.material, table.name, second.trim(), by.trim())
      onDone(`${table.file} 已标记为 🟢 已双源核验`)
    } catch (e) {
      setError((e as RequestError).message)
    } finally {
      setBusy(false)
    }
  }

  const same = second.trim() && second.trim() === table.data_source.trim()

  return (
    <Modal title={`双源核验 · ${table.file}`} onClose={onClose}>
      <div className="mb-4">
        <Alert tone="info" title="为什么必须填第二信源">
          按 <span className="num">references/source_priority.md</span>，关键工况系数需
          「≥ 第 2 级信源 + 至少 2 个独立信源」。没有第二信源的绿灯无从追溯，
          比黄灯更危险——它会让人以为这个数已经被验证过了。
        </Alert>
      </div>

      <div className="space-y-3">
        <Field label="当前状态">
          <ConfidenceBadge level={table.confidence} />
        </Field>

        <Field label="主信源（已有）">
          <div className="card2 p-2.5 text-[12px]">{table.data_source || '—'}</div>
        </Field>

        <div>
          <label className="text-[12px] block mb-1.5">
            第二信源 <span style={{ color: 'var(--err)' }}>*</span>
          </label>
          <input className={`inp ${same ? 'err' : ''}`} value={second} autoFocus
                 placeholder="例：GB/T 11362-2021 表 6（纸质原件核对）"
                 onChange={e => setSecond(e.target.value)} />
          <div className="text-[11px] mt-1" style={{ color: same ? 'var(--err)' : 'var(--sub)' }}>
            {same ? '第二信源与主信源相同，这不构成交叉验证'
                  : '必须是与主信源相互独立的另一份资料：标准原件、另一本手册、或厂商样本'}
          </div>
        </div>

        <div>
          <label className="text-[12px] block mb-1.5">核验人（可选）</label>
          <input className="inp" value={by} placeholder="留个名字，方便日后追溯"
                 onChange={e => setBy(e.target.value)} />
        </div>

        {error && <Alert tone="err" title="核验未通过">{error}</Alert>}
      </div>

      <div className="flex gap-2 pt-4 mt-4 border-t" style={{ borderColor: 'var(--line)' }}>
        <button className="btn" onClick={onClose}>取消</button>
        <div className="flex-1" />
        <button className="btn btn-ok" disabled={!second.trim() || !!same || busy}
                onClick={() => void submit()}>
          {busy ? '保存中…' : '确认已双源核验'}
        </button>
      </div>
    </Modal>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-[12px] mb-1.5">{label}</div>
      {children}
    </div>
  )
}
