"use client";

import { Activity, ChevronRight, LayoutDashboard, Menu, X } from "lucide-react";
import { motion, useReducedMotion } from "motion/react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";

type SidebarProps = {
  email: string;
  inSettings: boolean;
  navigate: (view?: "dashboard" | "settings") => void;
};

function Brand() {
  return (
    <Link href="/" className="brand" aria-label="PulseGrid home">
      <span className="brand-icon">
        <Activity size={22} aria-hidden="true" />
      </span>
      <span className="sidebar-label">PulseGrid</span>
    </Link>
  );
}

function SidebarLinks({ email, inSettings, navigate }: SidebarProps) {
  return (
    <>
      <nav aria-label="Main navigation">
        <button
          type="button"
          aria-label="Dashboard"
          title="Dashboard"
          aria-current={!inSettings ? "page" : undefined}
          className={!inSettings ? "active" : ""}
          onClick={() => navigate("dashboard")}
        >
          <LayoutDashboard size={18} aria-hidden="true" />
          <span className="sidebar-label">Dashboard</span>
        </button>
      </nav>
      <div className="sidebar-bottom">
        <button
          type="button"
          className="profile"
          aria-label="Your account settings"
          title="Your account settings"
          aria-current={inSettings ? "page" : undefined}
          onClick={() => navigate("settings")}
        >
          <span className="avatar" aria-hidden="true">
            {email[0].toUpperCase()}
          </span>
          <span className="sidebar-label truncate">
            {email}
            <small>Your account</small>
          </span>
          <ChevronRight
            size={16}
            className="sidebar-label"
            aria-hidden="true"
          />
        </button>
      </div>
    </>
  );
}

// Aceternity-inspired rail and sliding mobile menu.
export function WorkspaceSidebar(props: SidebarProps) {
  const [hovered, setHovered] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const menuButton = useRef<HTMLButtonElement>(null);
  const reducedMotion = useReducedMotion();

  useEffect(() => {
    const desktop = window.matchMedia("(width > 800px)");
    const closeOnDesktop = () => {
      if (desktop.matches) dialog.current?.close();
    };
    desktop.addEventListener("change", closeOnDesktop);
    return () => desktop.removeEventListener("change", closeOnDesktop);
  }, []);

  return (
    <>
      <motion.aside
        className="sidebar workspace-sidebar"
        id="workspace-sidebar"
        data-hovered={hovered}
        onHoverStart={() => setHovered(true)}
        onHoverEnd={() => setHovered(false)}
      >
        <Brand />
        <SidebarLinks {...props} />
      </motion.aside>
      <header className="mobile-sidebar">
        <Brand />
        <button
          type="button"
          className="sidebar-toggle"
          ref={menuButton}
          aria-label="Open navigation menu"
          aria-haspopup="dialog"
          aria-controls="mobile-sidebar-drawer"
          aria-expanded={menuOpen}
          onClick={() => {
            dialog.current?.showModal();
            setMenuOpen(true);
          }}
        >
          <Menu size={21} aria-hidden="true" />
        </button>
        <button
          type="button"
          className="sidebar-toggle"
          aria-label="Your account settings"
          aria-current={props.inSettings ? "page" : undefined}
          onClick={() => props.navigate("settings")}
        >
          <span className="avatar" aria-hidden="true">
            {props.email[0].toUpperCase()}
          </span>
        </button>
        <dialog
          ref={dialog}
          id="mobile-sidebar-drawer"
          className="mobile-sidebar-drawer"
          aria-label="Navigation menu"
          onClose={() => {
            setMenuOpen(false);
            menuButton.current?.focus();
          }}
        >
          <motion.div
            className="mobile-sidebar-panel"
            initial={false}
            animate={{ x: menuOpen || reducedMotion ? 0 : "-100%" }}
            transition={{ duration: reducedMotion ? 0 : 0.24, ease: "easeOut" }}
          >
            <div className="mobile-sidebar-heading">
              <strong>PulseGrid</strong>
              <button
                type="button"
                className="sidebar-toggle"
                aria-label="Close navigation menu"
                autoFocus
                onClick={() => dialog.current?.close()}
              >
                <X size={21} aria-hidden="true" />
              </button>
            </div>
            <SidebarLinks
              {...props}
              navigate={(view) => {
                dialog.current?.close();
                props.navigate(view);
              }}
            />
          </motion.div>
        </dialog>
      </header>
    </>
  );
}
