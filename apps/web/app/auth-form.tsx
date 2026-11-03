import { ArrowRight } from "lucide-react";
import { cloneElement, useId, type ReactElement, type ReactNode } from "react";

// Field glow and button gradient adapted from https://ui.aceternity.com/components/signup-form.
export function AuthField({
  label,
  children,
}: {
  label: string;
  children: ReactElement<{ id?: string }>;
}) {
  const id = useId();
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <div
        className="auth-field"
        onPointerMove={({ currentTarget, clientX, clientY }) => {
          const bounds = currentTarget.getBoundingClientRect();
          currentTarget.style.setProperty(
            "--pointer-x",
            `${clientX - bounds.left}px`,
          );
          currentTarget.style.setProperty(
            "--pointer-y",
            `${clientY - bounds.top}px`,
          );
        }}
      >
        {cloneElement(children, { id })}
      </div>
    </div>
  );
}

export function AuthButton({
  children,
  disabled,
  onClick,
}: {
  children: ReactNode;
  disabled?: boolean;
  onClick?: () => void;
}) {
  return (
    <button
      className="primary wide auth-submit"
      type={onClick ? "button" : "submit"}
      disabled={disabled}
      onClick={onClick}
    >
      {children}
      <ArrowRight size={16} aria-hidden="true" />
    </button>
  );
}
