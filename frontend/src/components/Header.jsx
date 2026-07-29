import { useEffect, useRef, useState } from 'react';
import { navLinks } from '../data/options.js';
import { ThemeToggle } from './ThemeToggle.jsx';

// The channel the pipeline publishes to. Uses the /channel/<id> form rather than
// the handle: the ID is permanent, whereas a handle is released the moment it is
// changed and can then be claimed by anyone.
const channelUrl =
    import.meta.env.VITE_FLUX_CHANNEL_URL?.trim() ||
    'https://www.youtube.com/channel/UCH8ePQPiHZfqYYgE2Wk4UCw';

function YouTubeIcon({ className }) {
    return (
        <svg viewBox="0 0 24 24" fill="currentColor" className={className}>
            <path d="M23.5 6.2a3 3 0 0 0-2.1-2.1C19.5 3.6 12 3.6 12 3.6s-7.5 0-9.4.5A3 3 0 0 0 .5 6.2 31.4 31.4 0 0 0 0 12a31.4 31.4 0 0 0 .5 5.8 3 3 0 0 0 2.1 2.1c1.9.5 9.4.5 9.4.5s7.5 0 9.4-.5a3 3 0 0 0 2.1-2.1A31.4 31.4 0 0 0 24 12a31.4 31.4 0 0 0-.5-5.8zM9.6 15.6V8.4l6.2 3.6-6.2 3.6z" />
        </svg>
    );
}

function Logo() {
    return (
        <span className="grid h-9 w-9 place-items-center rounded-full border border-tint/15 bg-tint/[0.06] text-txt">
            <svg viewBox="0 0 64 64" className="h-6 w-6" fill="none">
                {/* automation loop */}
                <path
                    d="M46.5 28.1 A15 15 0 1 1 35.9 17.5"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="3.2"
                    strokeLinecap="round"
                />
                <path d="M31.6 17.2 L37.2 15.4 L38.6 21.2 Z" fill="currentColor" />
                {/* play triangle (video) */}
                <path d="M24 25.5 L33 32 L24 38.5 Z" fill="currentColor" />
                {/* audio waveform (podcast) */}
                <g stroke="currentColor" strokeWidth="2.6" strokeLinecap="round">
                    <path d="M37 28.5 V35.5" />
                    <path d="M41 25 V39" />
                    <path d="M45 29.5 V34.5" />
                </g>
            </svg>
        </span>
    );
}

function SearchIcon({ className }) {
    return (
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className={className}>
            <circle cx="11" cy="11" r="7" />
            <path d="m20 20-3.5-3.5" />
        </svg>
    );
}

export function Header({ onNavigate, searchQuery = '', onSearchChange }) {
    const [menuOpen, setMenuOpen] = useState(false);
    const headerRef = useRef(null);

    function handleNav(id) {
        setMenuOpen(false);
        onNavigate?.(id);
    }

    // Close on Escape, on a tap outside the header, and when the viewport grows
    // past the lg breakpoint — otherwise rotating a phone leaves an open panel
    // stranded behind the desktop nav.
    useEffect(() => {
        if (!menuOpen) return undefined;

        const onKey = (e) => e.key === 'Escape' && setMenuOpen(false);
        const onPointerDown = (e) => {
            if (!headerRef.current?.contains(e.target)) setMenuOpen(false);
        };
        const desktop = window.matchMedia('(min-width: 1024px)');
        const onBreakpoint = (e) => e.matches && setMenuOpen(false);

        document.addEventListener('keydown', onKey);
        document.addEventListener('mousedown', onPointerDown);
        document.addEventListener('touchstart', onPointerDown, { passive: true });
        desktop.addEventListener('change', onBreakpoint);
        return () => {
            document.removeEventListener('keydown', onKey);
            document.removeEventListener('mousedown', onPointerDown);
            document.removeEventListener('touchstart', onPointerDown);
            desktop.removeEventListener('change', onBreakpoint);
        };
    }, [menuOpen]);

    return (
        <header
            ref={headerRef}
            className="sticky top-0 z-50 border-b border-tint/10 bg-panel/80 backdrop-blur-xl"
        >
            <div className="mx-auto flex w-full max-w-[1600px] items-center gap-3 px-4 py-3 sm:gap-6 sm:px-5 lg:px-8">
                {/* Brand */}
                <button
                    type="button"
                    onClick={() => handleNav('top')}
                    className="flex shrink-0 items-center gap-2.5 sm:gap-3"
                >
                    <Logo />
                    <span className="display text-base font-bold tracking-tight sm:text-lg">
                        FLUX <span className="text-muted">AI</span>
                    </span>
                </button>

                {/* Nav */}
                <nav className="hidden items-center gap-7 lg:flex">
                    {navLinks.map((link) => (
                        <button
                            key={link.id}
                            type="button"
                            onClick={() => handleNav(link.id)}
                            className="text-xs font-semibold uppercase tracking-[0.14em] text-muted transition-colors hover:text-txt"
                        >
                            {link.label}
                        </button>
                    ))}
                </nav>

                {/* Search */}
                <div className="relative ml-auto hidden max-w-xl flex-1 items-center md:flex">
                    <SearchIcon className="pointer-events-none absolute left-4 h-4 w-4 text-faint" />
                    <input
                        type="search"
                        value={searchQuery}
                        onChange={(e) => onSearchChange?.(e.target.value)}
                        placeholder="Search generated videos by topic..."
                        className="field-input w-full rounded-xl border-tint/10 bg-tint/[0.04] py-2.5 pl-11 pr-4 text-sm"
                    />
                </div>

                <div className="hidden shrink-0 items-center gap-2 lg:flex">
                    <ThemeToggle />
                    <a
                        href={channelUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                        title="UpperCircuit channel"
                        className="grid h-9 w-9 place-items-center rounded-full border border-tint/15 bg-tint/[0.06] text-txt transition-colors hover:bg-tint/[0.12]"
                    >
                        <YouTubeIcon className="h-4 w-4" />
                    </a>
                </div>

                <div className="ml-auto flex shrink-0 items-center gap-1.5 sm:gap-2 lg:hidden">
                    <ThemeToggle />
                    <button
                        type="button"
                        aria-label={menuOpen ? 'Close menu' : 'Open menu'}
                        aria-expanded={menuOpen}
                        aria-controls="mobile-menu"
                        onClick={() => setMenuOpen((open) => !open)}
                        className="grid h-10 w-10 place-items-center rounded-full border border-tint/15 bg-panel transition-colors hover:bg-tint/[0.08]"
                    >
                        {/* Bars are absolutely positioned so the morph to an X lands
                            exactly on centre — stacking them in a flex column made the
                            rotated arms miss each other by the gap height. */}
                        <span className="relative block h-4 w-5" aria-hidden="true">
                            <span
                                className={`absolute left-0 block h-0.5 w-5 rounded-full bg-txt transition-all duration-300 ease-out ${
                                    menuOpen ? 'top-1/2 -translate-y-1/2 rotate-45' : 'top-0.5'
                                }`}
                            />
                            <span
                                className={`absolute left-0 top-1/2 block h-0.5 w-5 -translate-y-1/2 rounded-full bg-txt transition-all duration-200 ease-out ${
                                    menuOpen ? 'scale-x-0 opacity-0' : 'scale-x-100 opacity-100'
                                }`}
                            />
                            <span
                                className={`absolute left-0 block h-0.5 w-5 rounded-full bg-txt transition-all duration-300 ease-out ${
                                    menuOpen ? 'top-1/2 -translate-y-1/2 -rotate-45' : 'bottom-0.5'
                                }`}
                            />
                        </span>
                    </button>
                </div>
            </div>

            {/*
              Animating to auto height needs a measured pixel value, so this uses the
              grid-rows 0fr -> 1fr technique instead: the row resolves to the child's
              natural height and the transition is pure CSS, with no layout thrash and
              no jump when the content length changes.
            */}
            <div
                id="mobile-menu"
                className={`grid overflow-hidden transition-[grid-template-rows,opacity] duration-300 ease-out motion-reduce:transition-none lg:hidden ${
                    menuOpen ? 'grid-rows-[1fr] opacity-100' : 'grid-rows-[0fr] opacity-0'
                }`}
            >
                <div className="min-h-0">
                    <div className="mx-4 mb-3 glass p-3 sm:mx-5">
                        {/* Search lives here because the desktop field is hidden below
                            md — without it the library was unsearchable on a phone. */}
                        <div className="relative mb-2 flex items-center md:hidden">
                            <SearchIcon className="pointer-events-none absolute left-4 h-4 w-4 text-faint" />
                            <input
                                type="search"
                                value={searchQuery}
                                onChange={(e) => onSearchChange?.(e.target.value)}
                                placeholder="Search videos by topic..."
                                tabIndex={menuOpen ? 0 : -1}
                                className="field-input w-full rounded-xl border-tint/10 bg-tint/[0.04] py-2.5 pl-11 pr-4 text-sm"
                            />
                        </div>

                        {navLinks.map((link) => (
                            <button
                                key={link.id}
                                type="button"
                                onClick={() => handleNav(link.id)}
                                tabIndex={menuOpen ? 0 : -1}
                                className="block w-full rounded-xl px-4 py-3 text-left text-sm font-medium text-muted transition-colors hover:bg-tint/[0.06] hover:text-txt"
                            >
                                {link.label}
                            </button>
                        ))}
                        <a
                            href={channelUrl}
                            target="_blank"
                            rel="noopener noreferrer"
                            tabIndex={menuOpen ? 0 : -1}
                            className="btn-light mt-2 flex w-full items-center justify-center gap-2"
                        >
                            <YouTubeIcon className="h-4 w-4" />
                            UpperCircuit Channel
                        </a>
                    </div>
                </div>
            </div>
        </header>
    );
}
