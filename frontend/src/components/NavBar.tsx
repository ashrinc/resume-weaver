"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  { href: "/", label: "Tailor" },
  { href: "/archive", label: "Archive" },
];

export function NavBar() {
  const pathname = usePathname();
  return (
    <nav className="nav">
      <Link href="/" className="nav-brand">
        <span className="nav-brand-prompt">&gt;</span> resume tailr
        <span className="nav-brand-cursor" aria-hidden="true">
          _
        </span>
      </Link>
      <div className="nav-links">
        {LINKS.map((link) => (
          <Link
            key={link.href}
            href={link.href}
            className={`nav-link${pathname === link.href ? " nav-link-active" : ""}`}
            aria-current={pathname === link.href ? "page" : undefined}
          >
            {link.label}
          </Link>
        ))}
      </div>
    </nav>
  );
}
