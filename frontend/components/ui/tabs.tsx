"use client";

import React, {
  createContext,
  useContext,
  ReactNode,
  HTMLAttributes,
} from "react";
import clsx from "clsx";

type TabsContextValue = {
  value: string;
  onChange?: (value: string) => void;
};

const TabsContext = createContext<TabsContextValue | null>(null);

type TabsProps = {
  value: string;
  onValueChange?: (value: string) => void;
  children: ReactNode;
  className?: string;
};

export function Tabs({ value, onValueChange, children, className }: TabsProps) {
  return (
    <TabsContext.Provider value={{ value, onChange: onValueChange }}>
      <div className={className}>{children}</div>
    </TabsContext.Provider>
  );
}

type TabsListProps = HTMLAttributes<HTMLDivElement>;

export function TabsList({ className, ...props }: TabsListProps) {
  return (
    <div
      className={clsx(
        "inline-flex items-center gap-1 rounded-full border border-slate-800/80 bg-slate-900/80 p-1",
        className
      )}
      {...props}
    />
  );
}

type TabsTriggerProps = {
  value: string;
  children: ReactNode;
  className?: string;
} & React.ButtonHTMLAttributes<HTMLButtonElement>;

export function TabsTrigger({
  value,
  children,
  className,
  ...props
}: TabsTriggerProps) {
  const ctx = useContext(TabsContext);
  const active = ctx?.value === value;

  return (
    <button
      type="button"
      onClick={() => ctx?.onChange?.(value)}
      className={clsx(
        "px-3 py-1 text-xs font-medium rounded-full transition",
        active
          ? "bg-sky-500/90 text-slate-900 shadow-sm"
          : "text-slate-300 hover:bg-slate-800/80",
        className
      )}
      {...props}
    >
      {children}
    </button>
  );
}

type TabsContentProps = {
  value: string;
  children: ReactNode;
  className?: string;
};

export function TabsContent({ value, children, className }: TabsContentProps) {
  const ctx = useContext(TabsContext);
  if (!ctx || ctx.value !== value) return null;

  return <div className={className}>{children}</div>;
}
