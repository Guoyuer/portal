"use client";

// ── Skeleton primitives ─────────────────────────────────────────────────

function Bone({ className = "" }: { className?: string }) {
  return <div className={`animate-pulse rounded-md bg-foreground/8 ${className}`} />;
}

// ── Finance page skeleton ───────────────────────────────────────────────

export function FinanceSkeleton() {
  return (
    <div className="max-w-5xl mx-auto space-y-10">
      {/* Header */}
      <div>
        <Bone className="h-7 w-48" />
        <Bone className="h-4 w-64 mt-2" />
      </div>

      {/* Metric cards */}
      <div className="grid grid-cols-3 gap-3">
        {Array.from({ length: 3 }, (_, i) => (
          <div key={i} className="liquid-glass p-4 space-y-3">
            <Bone className="h-3 w-20" />
            <Bone className="h-6 w-28" />
            <Bone className="h-3 w-16" />
          </div>
        ))}
      </div>

      {/* Timemachine chart */}
      <div className="liquid-glass p-4 sm:p-5 space-y-3">
        <div className="flex justify-between">
          <Bone className="h-4 w-32" />
          <Bone className="h-5 w-24" />
        </div>
        <Bone className="h-2 w-full rounded-full" />
        <div className="grid grid-cols-4 gap-2">
          {Array.from({ length: 4 }, (_, i) => (
            <div key={i} className="space-y-1">
              <Bone className="h-3 w-10" />
              <Bone className="h-4 w-14" />
            </div>
          ))}
        </div>
        <Bone className="h-[240px] w-full mt-4" />
      </div>

      {/* Section placeholder */}
      <div>
        <Bone className="h-5 w-24 mb-3" />
        <div className="liquid-glass p-4 space-y-2">
          {Array.from({ length: 4 }, (_, i) => (
            <Bone key={i} className="h-4 w-full" />
          ))}
        </div>
      </div>
    </div>
  );
}

// ── Econ page skeleton ──────────────────────────────────────────────────

export function EconSkeleton() {
  return (
    <div className="max-w-5xl mx-auto space-y-10">
      <Bone className="h-7 w-56" />
      <Bone className="h-3 w-40" />

      {/* Macro cards */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {Array.from({ length: 4 }, (_, i) => (
          <div key={i} className="liquid-glass p-4 space-y-2">
            <Bone className="h-3 w-16" />
            <Bone className="h-5 w-20" />
          </div>
        ))}
      </div>

      {/* Chart placeholder */}
      <div>
        <Bone className="h-5 w-32 mb-3" />
        <div className="liquid-glass p-4">
          <Bone className="h-[280px] w-full" />
        </div>
      </div>
    </div>
  );
}
