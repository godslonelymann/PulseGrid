import { Check, MoreHorizontal, X } from "lucide-react";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";

export function Modal({
  title,
  children,
  close,
  busy = false,
}: {
  title: string;
  children: ReactNode;
  close: () => void;
  busy?: boolean;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const node = dialog.current;
    const opener = document.activeElement;
    node?.showModal();
    node?.querySelector<HTMLElement>("[data-dialog-focus]")?.focus();
    const viewport = window.visualViewport;
    const resize = () => {
      if (!viewport || viewport.scale !== 1) return;
      node?.style.setProperty("--dialog-top", `${viewport.offsetTop}px`);
      node?.style.setProperty(
        "--dialog-bottom",
        `${Math.max(0, window.innerHeight - viewport.height - viewport.offsetTop)}px`,
      );
    };
    viewport?.addEventListener("resize", resize);
    viewport?.addEventListener("scroll", resize);
    resize();
    return () => {
      viewport?.removeEventListener("resize", resize);
      viewport?.removeEventListener("scroll", resize);
      node?.close();
      if (opener instanceof HTMLElement && opener.isConnected) opener.focus();
    };
  }, []);
  return (
    <dialog
      className="modal"
      ref={dialog}
      aria-labelledby={titleId}
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) close();
      }}
      onClick={(event) => {
        if (event.target === event.currentTarget && !busy) close();
      }}
    >
      <div className="modal-body" aria-busy={busy}>
        <div className="modal-heading">
          <h2 id={titleId}>{title}</h2>
          <button
            type="button"
            className="icon"
            aria-label="Close dialog"
            disabled={busy}
            onClick={close}
          >
            <X size={20} />
          </button>
        </div>
        {children}
      </div>
    </dialog>
  );
}

export function Toast({
  message,
  dismiss,
}: {
  message: string;
  dismiss: () => void;
}) {
  useEffect(() => {
    if (!message) return;
    const timer = window.setTimeout(dismiss, 5000);
    return () => window.clearTimeout(timer);
  }, [message, dismiss]);
  return (
    <div className={message ? "toast" : undefined} role="status">
      {message && (
        <>
          <Check size={16} aria-hidden="true" />
          {message}
        </>
      )}
    </div>
  );
}

export function ServiceMenu({
  name,
  actions,
}: {
  name: string;
  actions: {
    label: string;
    icon: ReactNode;
    disabled?: boolean;
    run: () => void;
  }[];
}) {
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const id = useId();
  const [open, setOpen] = useState(false);
  const close = () => {
    setOpen(false);
    trigger.current?.focus();
  };
  useEffect(() => {
    if (!open) return;
    const dismiss = (event: PointerEvent) => {
      if (event.target instanceof Node && !root.current?.contains(event.target))
        setOpen(false);
    };
    document.addEventListener("pointerdown", dismiss);
    return () => document.removeEventListener("pointerdown", dismiss);
  }, [open]);
  return (
    <div
      ref={root}
      className="service-menu"
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false);
      }}
      onKeyDown={(event) => {
        if (event.key === "Escape" && open) {
          event.stopPropagation();
          close();
        }
      }}
    >
      <button
        ref={trigger}
        type="button"
        className="icon"
        aria-label={`Actions for ${name}`}
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        onClick={() => setOpen(!open)}
      >
        <MoreHorizontal size={19} aria-hidden="true" />
      </button>
      {open && (
        <div id={id} className="service-menu-panel">
          {actions.map(({ label, icon, disabled, run }) => (
            <button
              key={label}
              type="button"
              aria-label={`${label} ${name}`}
              disabled={disabled}
              onClick={() => {
                close();
                run();
              }}
            >
              {icon} {label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
