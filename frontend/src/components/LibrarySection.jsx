import { VideoCard } from './VideoCard.jsx';
import { titleFromFilename } from '../lib/payload.js';

export function LibrarySection({
    videos,
    searchQuery,
    isGenerating,
    isPolling,
    newFilename,
    onRequestDelete,
    onPlay,
}) {
    const query = (searchQuery || '').trim().toLowerCase();
    const filtered = query
        ? videos.filter(
              (video) =>
                  video.name.toLowerCase().includes(query) ||
                  titleFromFilename(video.name).toLowerCase().includes(query)
          )
        : videos;

    const showAwaiting = isGenerating || isPolling;

    return (
        <section id="library" className="mx-auto w-full max-w-[1600px] px-5 py-14 lg:px-8">
            <div className="mb-2 flex items-center gap-3">
                <span className="grid h-8 w-8 place-items-center rounded-lg border border-tint/15 bg-tint/[0.06]">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" className="h-4 w-4" strokeLinecap="round" strokeLinejoin="round">
                        <rect x="3" y="5" width="18" height="14" rx="2" />
                        <path d="m10 9 5 3-5 3z" />
                    </svg>
                </span>
                <h2 className="display text-2xl font-bold tracking-tight sm:text-3xl">
                    Generated News Videos
                </h2>
            </div>
            <p className="text-xs font-semibold uppercase tracking-[0.14em] text-muted">
                AI-generated videos from Economic Times articles appear here automatically
            </p>

            <div className="mt-6 border-t border-tint/10 pt-8">
                {filtered.length === 0 && !showAwaiting ? (
                    <div className="rounded-2xl border border-tint/10 p-14 text-center">
                        <p className="display text-xl font-semibold text-txt">No videos yet</p>
                        <p className="mt-2 text-sm text-muted">
                            {query
                                ? 'Nothing matches that search.'
                                : 'Run the automation above to generate your first video.'}
                        </p>
                    </div>
                ) : (
                    <div className="grid gap-x-6 gap-y-8 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                        {showAwaiting && (
                            <article className="flex flex-col">
                                <div className="grid aspect-[4/3] w-full place-items-center rounded-xl border border-tint/10 bg-tint/[0.03]">
                                    <div className="flex flex-col items-center gap-2">
                                        <span className="h-2.5 w-2.5 animate-pulse rounded-full bg-accent" />
                                        <p className="text-sm font-semibold text-txt">Rendering</p>
                                    </div>
                                </div>
                                <p className="mt-3 truncate text-sm font-semibold text-muted">
                                    Generating…
                                </p>
                            </article>
                        )}
                        {filtered.map((video) => (
                            <VideoCard
                                key={video.name}
                                video={video}
                                isNew={video.name === newFilename}
                                onRequestDelete={onRequestDelete}
                                onPlay={onPlay}
                            />
                        ))}
                    </div>
                )}
            </div>
        </section>
    );
}
