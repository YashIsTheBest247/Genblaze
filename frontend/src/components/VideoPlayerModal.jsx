import { useEffect, useRef } from 'react';
import { mediaUrl } from '../api/client.js';
import { titleFromFilename } from '../lib/payload.js';

export function VideoPlayerModal({ video, onClose }) {
    const videoRef = useRef(null);

    // Close on Escape, and lock background scroll while open.
    useEffect(() => {
        if (!video) return undefined;
        const onKey = (e) => e.key === 'Escape' && onClose?.();
        window.addEventListener('keydown', onKey);
        const prevOverflow = document.body.style.overflow;
        document.body.style.overflow = 'hidden';
        return () => {
            window.removeEventListener('keydown', onKey);
            document.body.style.overflow = prevOverflow;
        };
    }, [video, onClose]);

    if (!video) return null;

    const src = mediaUrl(video.path);
    const poster = video.thumbnail ? mediaUrl(video.thumbnail) : undefined;
    const title = titleFromFilename(video.name);

    return (
        <div className="fixed inset-0 z-[110] flex flex-col items-center justify-center p-4">
            {/* backdrop */}
            <div
                onClick={onClose}
                className="absolute inset-0 animate-fadeIn bg-black/92 backdrop-blur-md"
            />

            {/* close */}
            <button
                type="button"
                onClick={onClose}
                aria-label="Close"
                className="absolute right-4 top-4 z-10 grid h-11 w-11 place-items-center rounded-full border border-white/15 bg-white/10 text-white backdrop-blur transition-colors hover:bg-white/20"
            >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="h-5 w-5" strokeLinecap="round">
                    <path d="M18 6 6 18M6 6l12 12" />
                </svg>
            </button>

            {/* player — fits the viewport without cropping or distortion */}
            <video
                ref={videoRef}
                src={src}
                poster={poster}
                controls
                autoPlay
                playsInline
                className="relative max-h-[88vh] max-w-[95vw] animate-popIn rounded-2xl bg-black object-contain shadow-card"
                onClick={(e) => e.stopPropagation()}
            >
                Your browser does not support embedded video.
            </video>

            <p className="relative mt-4 max-w-[90vw] truncate text-center text-sm font-medium text-white/80">
                {title}
            </p>
        </div>
    );
}
