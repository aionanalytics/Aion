import * as React from "react";

export interface InputProps
  extends React.InputHTMLAttributes<HTMLInputElement> {}

const Input = React.forwardRef<HTMLInputElement, InputProps>(
  ({ className = "", ...props }, ref) => {
    return (
      <input
        ref={ref}
        className={
          "flex h-8 w-full rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-sm text-white placeholder:text-slate-500 focus:outline-none focus:ring-2 focus:ring-blue-500 " +
          className
        }
        {...props}
      />
    );
  }
);

Input.displayName = "Input";

export { Input };
