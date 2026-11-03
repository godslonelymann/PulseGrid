// Authenticator slots and rolling digit motion adapted from Rare UI.
// Copyright (c) 2026 Swami Malode. See docs/RARE_UI_LICENSE.txt.
import { Eye, EyeOff } from "lucide-react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { useState, type ComponentProps } from "react";

type InputProps = ComponentProps<"input">;

export function PasswordInput(props: InputProps) {
  const [visible, setVisible] = useState(false);
  return (
    <div className="password-field">
      <input {...props} type={visible ? "text" : "password"} />
      <button
        type="button"
        className="icon"
        aria-label={
          props.autoComplete === "new-password"
            ? "Show new password"
            : "Show current password"
        }
        aria-pressed={visible}
        onClick={() => setVisible(!visible)}
      >
        {visible ? (
          <EyeOff size={17} aria-hidden="true" />
        ) : (
          <Eye size={17} aria-hidden="true" />
        )}
      </button>
    </div>
  );
}

const rollSpring = { type: "spring", stiffness: 500, damping: 34 } as const;

export function AuthenticatorCode(props: InputProps) {
  const [code, setCode] = useState("");
  const [caret, setCaret] = useState(0);
  const reduced = useReducedMotion();
  return (
    <div className="authenticator-code">
      <input
        {...props}
        name="code"
        type="text"
        inputMode="numeric"
        autoComplete="one-time-code"
        pattern="[0-9]{6}"
        required
        value={code}
        onSelect={(event) => setCaret(event.currentTarget.selectionStart ?? 0)}
        onChange={(event) => {
          const input = event.currentTarget;
          setCaret(
            input.value
              .slice(0, input.selectionStart ?? 0)
              .replace(/[^0-9]/g, "").length,
          );
          setCode(input.value.replace(/[^0-9]/g, "").slice(0, 6));
        }}
      />
      <div className="code-slots" aria-hidden="true">
        {Array.from({ length: 6 }, (_, index) => (
          <span
            className="code-slot"
            key={index}
            data-caret={index === Math.min(5, caret)}
          >
            <AnimatePresence initial={false}>
              {code[index] && (
                <motion.span
                  key={code[index]}
                  initial={reduced ? false : { y: "110%" }}
                  animate={{ y: 0 }}
                  exit={reduced ? undefined : { y: "-110%" }}
                  transition={reduced ? { duration: 0 } : rollSpring}
                >
                  {code[index]}
                </motion.span>
              )}
            </AnimatePresence>
          </span>
        ))}
      </div>
    </div>
  );
}
