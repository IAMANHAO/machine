import { useEffect, type ReactNode } from 'react'

export default function Modal({ title, onClose, children, width = 560 }: {
  title: string
  onClose: () => void
  children: ReactNode
  width?: number
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto scroll p-4 md:p-10"
         style={{ background: 'rgba(3,7,12,.72)', backdropFilter: 'blur(3px)' }}
         onClick={onClose}>
      <div className="card p-5 w-full my-auto" style={{ maxWidth: width }}
           onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-4 gap-3">
          <div className="text-[14px] font-semibold">{title}</div>
          <button className="btn text-[12px] py-1 px-2.5" onClick={onClose}>关闭</button>
        </div>
        {children}
      </div>
    </div>
  )
}
