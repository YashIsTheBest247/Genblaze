import { useState } from 'react';
import { mediaUrl } from '../api/client.js';
import { titleFromFilename } from '../lib/payload.js';

function formatDuration(seconds) {
    if (!seconds || Number.isNaN(seconds)) return null;
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

export function VideoCard({ video, isNew, onRequestDelete, onPlay }) {
    const title = titleFromFilename(video.name);
    const src = mediaUrl(video.path);
    const poster = video.thumbnail ? mediaUrl(video.thumbnail) : undefined;
    const [duration, setDuration] = useState(null);
    const [menuOpen, setMenuOpen] = useState(false);

    return (
        <article className="group flex flex-col">
            {/* thumbnail */}
            <div
                onClick={() => onPlay?.(video)}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && onPlay?.(video)}
                className="relative aspect-[4/3] w-full cursor-pointer overflow-hidden rounded-xl border border-tint/10 bg-black/60 transition-all duration-300 hover:border-tint/30 hover:shadow-card"
            >
                <video
                    preload="metadata"
                    src={src}
                    poster={poster}
                    muted
                    playsInline
                    onLoadedMetadata={(e) => setDuration(e.currentTarget.duration)}
                    className="pointer-events-none h-full w-full object-cover opacity-90 transition-transform duration-500 group-hover:scale-105"
                />

                {/* play overlay */}
                <span className="pointer-events-none absolute inset-0 grid place-items-center bg-black/10 opacity-0 transition-opacity duration-300 group-hover:opacity-100">
                    <span className="grid h-12 w-12 place-items-center rounded-full bg-black/60 text-white backdrop-blur-sm">
                        <svg viewBox="0 0 24 24" fill="currentColor" className="h-5 w-5 translate-x-[1px]">
                            <path d="M8 5v14l11-7z" />
                        </svg>
                    </span>
                </span>

                {isNew && (
                    <span className="pointer-events-none absolute left-3 top-3 rounded bg-accent px-2 py-1 text-[0.55rem] font-bold uppercase tracking-[0.1em] text-onprimary">
                        New
                    </span>
                )}

                {duration && (
                    <span className="pointer-events-none absolute bottom-3 right-3 rounded bg-black/75 px-2 py-1 text-[0.65rem] font-semibold text-white">
                        {formatDuration(duration)}
                    </span>
                )}
            </div>

            {/* meta */}
            <div className="mt-3 flex items-start justify-between gap-2">
                <div className="min-w-0">
                    <h3 className="truncate text-sm font-semibold text-txt">{title}</h3>
                    <p className="mt-0.5 text-[0.65rem] font-semibold uppercase tracking-[0.12em] text-muted">
                        Generated · Vertical short
                    </p>
                </div>

                <div className="relative shrink-0">
                    <button
                        type="button"
                        aria-label="More actions"
                        onClick={() => setMenuOpen((v) => !v)}
                        onBlur={() => window.setTimeout(() => setMenuOpen(false), 150)}
                        className="grid h-7 w-7 place-items-center rounded-md text-muted transition-colors hover:bg-tint/[0.08] hover:text-txt"
                    >
                        <svg viewBox="0 0 24 24" fill="currentColor" className="h-4 w-4">
                            <circle cx="12" cy="5" r="1.6" />
                            <circle cx="12" cy="12" r="1.6" />
                            <circle cx="12" cy="19" r="1.6" />
                        </svg>
                    </button>

                    {menuOpen && (
                        <div className="absolute right-0 top-8 z-20 w-36 animate-popIn overflow-hidden rounded-xl border border-tint/15 bg-panel shadow-card">
                            <a
                                href={src}
                                download
                                className="block px-3 py-2 text-xs font-medium text-txt transition-colors hover:bg-tint/[0.08]"
                            >
                                Download
                            </a>
                            <button
                                type="button"
                                onClick={() => onRequestDelete?.({ name: video.name, title })}
                                className="block w-full px-3 py-2 text-left text-xs font-medium text-red-400 transition-colors hover:bg-red-500/10"
                            >
                                Delete
                            </button>
                        </div>
                    )}
                </div>
            </div>
        </article>
    );
}
