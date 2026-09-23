import { useState } from 'react'
import { api, RequestError } from '../api'
import type { Gap } from '../types'
import { Alert } from './ui'
import Modal from './Modal'

interface Row { y: string; z: string }

/**
 * 数据点补录。缺口清单已经算出了「往哪张表的哪一行补哪些点」，
 * 这里只需要人把查到的数值填进来。
 *
 * 一条硬规则：录入必须同时说清数据出处。这个产品卖的是可追溯，
 * 一个来路不明的数字进了知识库，后面每一次引用它的选型都被污染了。
 */
export default function PointsDialog({ material, gap, onClose, onDone }: {
  material: string
  gap: Gap
  onClose: () => void
  onDone: (msg: string) => void
}) {
  const t = gap.target
  const ready = !!(t.table && t.group && t.x_field && t.y_field && t.z_field
                   && t.x_value !== undefined)

  const [rows, setRows] = useState<Row[]>(
    (t.y_missing?.length ? t.y_missing : [undefined]).map(y => ({
      y: y !== undefined ? String(y) : '', z: '',
    })))
  const [source, setSource] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  if (!ready) {
    return (
      <Modal title="无法补录" onClose={onClose}>
        <Alert tone="warn" title="这个缺口不是靠补数据点解决的">
          {gap.message}
          {gap.fix_hint && <div className="mt-2">→ {gap.fix_hint}</div>}
        </Alert>
      </Modal>
    )
  }

  const setRow = (i: number, patch: Partial<Row>) =>
    setRows(rs => rs.map((r, j) => (j === i ? { ...r, ...patch } : r)))

  const valid = rows.filter(r => r.y.trim() !== '' && r.z.trim() !== ''
                                 && !Number.isNaN(Number(r.y)) && !Number.isNaN(Number(r.z)))
  const canSave = valid.length > 0 && source.trim().length >= 4

  const submit = async () => {
    setBusy(true); setError('')
    try {
      await api.upsertPoints(material, t.table!, {
        group: t.group!,
        rows_key: t.rows_key || 'examples',
        x_field: t.x_field!,
        x_value: Number(t.x_value),
        y_field: t.y_field!,
        z_field: t.z_field!,
        points: valid.map(r => ({ [t.y_field!]: Number(r.y), [t.z_field!]: Number(r.z) })),
      })
      // 数据出处单独记一笔，别让它只活在提交者的脑子里
      await api.setSources(material, t.table!, {
        note: `${t.group} ${t.x_field}=${t.x_value} 于 ${new Date().toISOString().slice(0, 10)} `
            + `补录 ${valid.length} 个点，出处：${source.trim()}；需双源核对后方可标 verified`,
      })
      onDone(`${t.table}.yaml 已补录 ${valid.length} 个数据点`)
    } catch (e) {
      setError((e as RequestError).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal title={`补录数据点 · ${t.table}.yaml`} onClose={onClose} width={640}>
      <div className="mb-4">
        <Alert tone="warn" title="录进来的数字会直接参与之后每一次选型">
          请照着标准原件或手册抄，不要估、不要按经验填。
          出处是必填的——这个产品的价值全在于每个数字都能追溯到来源。
        </Alert>
      </div>

      <div className="card2 p-3 mb-4 text-[12px] grid grid-cols-2 gap-y-1.5">
        <Kv k="分组" v={t.group!} />
        <Kv k="行坐标" v={`${t.x_field} = ${t.x_value}`} />
        <Kv k="自变量" v={t.y_field!} />
        <Kv k="因变量" v={t.z_field!} />
      </div>

      <div className="mb-3">
        <div className="grid grid-cols-[1fr_1fr_auto] gap-2 mb-1.5 text-[11px]"
             style={{ color: 'var(--sub)' }}>
          <div>{t.y_field}</div><div>{t.z_field}</div><div />
        </div>
        {rows.map((r, i) => (
          <div key={i} className="grid grid-cols-[1fr_1fr_auto] gap-2 mb-2">
            <input className="inp num" value={r.y} inputMode="decimal"
                   placeholder={t.y_field} onChange={e => setRow(i, { y: e.target.value })} />
            <input className="inp num" value={r.z} inputMode="decimal"
                   placeholder={t.z_field} onChange={e => setRow(i, { z: e.target.value })} />
            <button className="btn text-[12px] px-2.5" title="删除这一行"
                    onClick={() => setRows(rs => rs.filter((_, j) => j !== i))}>×</button>
          </div>
        ))}
        <button className="btn text-[12px] py-1 px-2.5"
                onClick={() => setRows(rs => [...rs, { y: '', z: '' }])}>
          + 再加一行
        </button>
      </div>

      <div className="mb-3">
        <label className="text-[12px] block mb-1.5">
          数据出处 <span style={{ color: 'var(--err)' }}>*</span>
        </label>
        <input className="inp" value={source}
               placeholder="例：GB/T 11362-2021 表 7，第 18 页，纸质原件"
               onChange={e => setSource(e.target.value)} />
        <div className="text-[11px] mt-1" style={{ color: 'var(--sub)' }}>
          会写进该表的待办记录。补录后状态仍为 🟡，需另找第二信源核对才能标 🟢。
        </div>
      </div>

      {error && <div className="mb-3"><Alert tone="err" title="保存失败">{error}</Alert></div>}

      <div className="flex gap-2 pt-4 border-t" style={{ borderColor: 'var(--line)' }}>
        <button className="btn" onClick={onClose}>取消</button>
        <div className="flex-1" />
        <span className="text-[11px] self-center" style={{ color: 'var(--sub)' }}>
          {valid.length} 个点待写入
        </span>
        <button className="btn btn-p" disabled={!canSave || busy} onClick={() => void submit()}>
          {busy ? '写入中…' : '写入数据表'}
        </button>
      </div>
    </Modal>
  )
}

function Kv({ k, v }: { k: string; v: string }) {
  return (
    <>
      <span style={{ color: 'var(--sub)' }}>{k}</span>
      <span className="num">{v}</span>
    </>
  )
}
