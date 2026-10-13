import clsx from 'clsx'

export default function Skeleton({ className = '' }) {
  return <div className={clsx('skeleton', className)} />
}

export function SkeletonCards({ count = 3 }) {
  return (
    <div className="space-y-3">
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="card p-4 space-y-2">
          <Skeleton className="h-4 w-1/3" />
          <Skeleton className="h-3 w-3/4" />
          <Skeleton className="h-3 w-1/2" />
        </div>
      ))}
    </div>
  )
}
