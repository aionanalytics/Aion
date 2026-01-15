"use client";

import * as React from "react";
import { ChevronDown } from "lucide-react";

export interface SelectOption {
  label: string;
  value: string;
}

interface SelectProps {
  value?: string;
  onChange?: (value: string) => void;
  options?: SelectOption[]; // <-- now optional + safe
  placeholder?: string;
  className?: string;
}

export function Select({
  value,
  onChange,
  options = [],          // <-- ALWAYS a real array
  placeholder = "Select…",
  className = "",
}: SelectProps) {
  const [open, setOpen] = React.useState(false);

  // Close dropdown when clicking outside
  const ref = React.useRef<HTMLDivElement>(null);
  React.useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  // Safe lookup (no `.find` on undefined)
  const selected =
    options.find((o) => o.value === (value ?? ""))?.label || placeholder;

  return (
    <div ref={ref} className={`relative text-sm ${className}`}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="
          w-full flex items-center justify-between rounded-md bg-slate-900 px-3 py-2
          border border-slate-700 text-white hover:border-slate-500
        "
      >
        <span className="truncate text-left">{selected}</span>
        <ChevronDown size={16} className="opacity-60" />
      </button>

      {open && (
        <div
          className="
            absolute z-50 mt-1 w-full rounded-md bg-slate-900 border border-slate-700 
            shadow-xl max-h-60 overflow-auto
          "
        >
          {options.length === 0 && (
            <div className="px-3 py-2 text-slate-400 text-sm">
              No options available
            </div>
          )}

          {options.map((opt) => (
            <div
              key={opt.value}
              onClick={() => {
                onChange?.(opt.value);
                setOpen(false);
              }}
              className={`
                px-3 py-2 cursor-pointer hover:bg-slate-800 
                text-white text-sm
                ${opt.value === value ? "bg-slate-800" : ""}
              `}
            >
              {opt.label}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
