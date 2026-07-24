import { useState } from 'react';
import { navLinks } from '../data/options.js';
import { ThemeToggle } from './ThemeToggle.jsx';

const channelUrl =
    import.meta.env.VITE_FLUX_CHANNEL_URL?.trim() ||
    'https://youtube.com/@echoesofmyindia?si=q-RwYc6p1VVCCtD_';

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

    function handleNav(id) {
        setMenuOpen(false);
        onNavigate?.(id);
    }

    return (
        <header className="sticky top-0 z-50 border-b border-tint/10 bg-panel/80 backdrop-blur-xl">
            <div className="mx-auto flex w-full max-w-[1600px] items-center gap-6 px-5 py-3 lg:px-8">
                {/* Brand */}
                <button
                    type="button"
                    onClick={() => handleNav('top')}
                    className="flex shrink-0 items-center gap-3"
                >
                    <Logo />
                    <span className="display text-lg font-bold tracking-tight">
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
                        title="Flux Channel"
                        className="grid h-9 w-9 place-items-center rounded-full border border-tint/15 bg-tint/[0.06] text-txt transition-colors hover:bg-tint/[0.12]"
                    >
                        <YouTubeIcon className="h-4 w-4" />
                    </a>
                </div>

                <div className="ml-auto flex items-center gap-2 lg:hidden">
                    <ThemeToggle />
                    <button
                        type="button"
                        aria-label="Toggle menu"
                        onClick={() => setMenuOpen((open) => !open)}
                        className="grid h-10 w-10 place-items-center rounded-full border border-tint/15 bg-panel"
                    >
                        <span className="flex flex-col gap-1.5">
                            <span className="h-0.5 w-4 bg-txt" />
                            <span className="h-0.5 w-4 bg-txt" />
                            <span className="h-0.5 w-4 bg-txt" />
                        </span>
                    </button>
                </div>
            </div>

            {menuOpen && (
                <div className="mx-5 mb-2 glass p-3 lg:hidden">
                    {navLinks.map((link) => (
                        <button
                            key={link.id}
                            type="button"
                            onClick={() => handleNav(link.id)}
                            className="block w-full rounded-xl px-4 py-2.5 text-left text-sm font-medium text-muted hover:bg-tint/[0.06] hover:text-txt"
                        >
                            {link.label}
                        </button>
                    ))}
                    <a
                        href={channelUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="btn-light mt-2 flex w-full items-center justify-center gap-2"
                    >
                        <YouTubeIcon className="h-4 w-4" />
                        Flux Channel
                    </a>
                </div>
            )}
        </header>
    );
}
