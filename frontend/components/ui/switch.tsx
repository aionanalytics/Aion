"use client";

import * as React from "react";

interface SwitchProps {
  checked?: boolean;
  onCheckedChange?: (value: boolean) => void;
  disabled?: boolean;
  className?: string;
}

export function Switch({
  checked = false,
  onCheckedChange,
  disabled = false,
  className = "",
}: SwitchProps) {
  return (
    <button
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => !disabled && onCheckedChange?.(!checked)}
      className={`
        relative inline-flex h-5 w-9 items-center rounded-full 
        transition-colors ${
          checked ? "bg-blue-600" : "bg-slate-700"
        } ${disabled ? "opacity-50 cursor-not-allowed" : "cursor-pointer"}
        ${className}
      `}
    >
      <span
        className={`
          inline-block h-4 w-4 transform bg-white rounded-full transition-transform
          ${checked ? "translate-x-4" : "translate-x-1"}
        `}
      />
    </button>
  );
}
