import * as React from "react";

export const Table = ({ className = "", ...props }: React.HTMLAttributes<HTMLTableElement>) => (
  <table
    className={`w-full text-sm border-collapse text-slate-200 ${className}`}
    {...props}
  />
);

export const TableHeader = ({
  className = "",
  ...props
}: React.HTMLAttributes<HTMLTableSectionElement>) => (
  <thead className={`bg-slate-900/70 ${className}`} {...props} />
);

export const TableBody = ({
  className = "",
  ...props
}: React.HTMLAttributes<HTMLTableSectionElement>) => (
  <tbody className={className} {...props} />
);

export const TableRow = ({
  className = "",
  ...props
}: React.HTMLAttributes<HTMLTableRowElement>) => (
  <tr
    className={`
      border-b border-slate-800 hover:bg-slate-800/40 transition-colors 
      ${className}
    `}
    {...props}
  />
);

export const TableHead = ({
  className = "",
  ...props
}: React.ThHTMLAttributes<HTMLTableCellElement>) => (
  <th
    className={`px-3 py-2 text-left font-medium text-xs text-slate-400 ${className}`}
    {...props}
  />
);

export const TableCell = ({
  className = "",
  ...props
}: React.TdHTMLAttributes<HTMLTableCellElement>) => (
  <td className={`px-3 py-2 align-middle ${className}`} {...props} />
);
