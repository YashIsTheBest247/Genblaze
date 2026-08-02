import { useCallback, useEffect, useState } from 'react';
import { fetchTrending } from '../api/trends.js';

const COUNT_OPTIONS = [1, 3, 5];

function RefreshIcon({ spinning }) {
    return (
        <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
            strokeLinejoin="round"
            className={`h-4 w-4 transition-transform ${spinning ? 'animate-spin' : 'group-hover:rotate-90'}`}
        >
            <path d="M21 12a9 9 0 1 1-2.64-6.36M21 3v5h-5" />
        </svg>
    );
}

function ArticleRow({ article, index, willProcess, onGenerate, busy }) {
    const score = Math.round((article.score ?? 0) * 100);
    const keywords = (article.keywords ?? []).slice(0, 3);

    return (
        // flex-wrap so the action column drops to its own line on a phone. Pinned
        // beside the title it left the headline roughly 190px on a 360px screen,
        // which broke every article into four cramped lines.
        <div
            className="group flex animate-fadeup flex-wrap items-start gap-x-3 gap-y-3 rounded-xl border border-tint/10 bg-tint/[0.02] p-3.5 transition-all hover:border-tint/25 hover:bg-tint/[0.05] sm:flex-nowrap sm:gap-4 sm:p-4"
            style={{ animationDelay: `${index * 60}ms`, animationFillMode: 'backwards' }}
        >
            <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full border border-tint/15 bg-tint/[0.06] text-xs font-bold text-txt">
                {index + 1}
            </span>

            <div className="min-w-0 flex-1">
                <a
                    href={article.link}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="block text-sm font-semibold leading-snug text-txt transition-colors hover:text-accent"
                >
                    {article.title}
                </a>
                <div className="mt-1.5 flex flex-wrap items-center gap-2">
                    <span className="text-[0.7rem] font-semibold text-muted">Score {score}</span>
                    <span className="text-[0.7rem] text-faint">{article.source}</span>
                </div>
                {keywords.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-1.5">
                        {keywords.map((kw) => (
                            <span
                                key={kw}
                                className="rounded-full border border-tint/10 bg-tint/[0.04] px-2 py-0.5 text-[0.6rem] text-muted"
                            >
                                {kw}
                            </span>
                        ))}
                    </div>
                )}
            </div>

            <div className="ml-11 flex w-full shrink-0 flex-row-reverse items-center justify-end gap-2 sm:ml-0 sm:w-auto sm:flex-col sm:items-end">
                {willProcess && (
                    <span className="rounded-full border border-accent/30 bg-accent/10 px-2.5 py-1 text-[0.55rem] font-bold uppercase tracking-[0.12em] text-accent">
                        Will process
                    </span>
                )}
                <button
                    type="button"
                    onClick={() => onGenerate?.(article)}
                    disabled={busy}
                    className="rounded-lg border border-tint/15 bg-tint/[0.05] px-3 py-1.5 text-[0.7rem] font-semibold text-txt transition-colors hover:bg-tint/[0.12] disabled:opacity-50"
                >
                    Generate
                </button>
            </div>
        </div>
    );
}

export function AutomationSection({ onGenerate, onRunAutomation, isGenerating }) {
    const [articles, setArticles] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [count, setCount] = useState(1);
    const [autoPublish, setAutoPublish] = useState(false);
    const [runNotice, setRunNotice] = useState('');

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            setArticles(await fetchTrending(10));
        } catch (err) {
            setError(err.message || 'Could not load trending articles.');
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        void load();
    }, [load]);

    // Show exactly the articles that will be processed. The list previously
    // rendered the whole fetched pool while the heading reported `count`, so the
    // label was a lie and the selector appeared to do nothing.
    // A pool larger than the max selectable count is still fetched so switching
    // 1 -> 3 -> 5 is instant and survives an article being filtered out.
    const visible = articles.slice(0, count);

    async function handleRun() {
        setRunNotice('');
        try {
            await onRunAutomation?.({ topN: count, autoPublish });
            setRunNotice(
                `Automation started — generating the top ${count} article${count > 1 ? 's' : ''}` +
                    (autoPublish ? ' and publishing to YouTube.' : '.')
            );
        } catch (err) {
            setRunNotice(err.message || 'Could not start automation.');
        }
    }

    return (
        <section id="automation" className="mx-auto w-full max-w-[1600px] scroll-mt-20 px-5 py-14 lg:px-8">
            <div className="glass rounded-2xl p-6 lg:p-8">
                {/* header */}
                <div className="mb-8 flex flex-wrap items-start justify-between gap-4">
                    <div className="flex items-start gap-4">
                        <span className="grid h-11 w-11 shrink-0 place-items-center rounded-xl border border-tint/15 bg-tint/[0.06]">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" className="h-5 w-5" strokeLinecap="round" strokeLinejoin="round">
                                <path d="m3 17 6-6 4 4 8-8" />
                                <path d="M15 7h6v6" />
                            </svg>
                        </span>
                        <div>
                            <h2 className="display text-2xl font-bold tracking-tight sm:text-3xl">
                                Economic Times Automation
                            </h2>
                            <p className="mt-1 text-sm text-muted">
                                Automatically convert trending news into viral videos
                            </p>
                        </div>
                    </div>
                    <button
                        type="button"
                        onClick={load}
                        disabled={loading}
                        className="group flex items-center gap-2 rounded-full border border-tint/15 bg-tint/[0.04] px-4 py-2 text-sm font-medium text-txt transition-colors hover:bg-tint/[0.1] disabled:opacity-60"
                    >
                        <RefreshIcon spinning={loading} />
                        Refresh
                    </button>
                </div>

                {/* controls */}
                <div className="mb-6 divide-y divide-tint/10 rounded-xl border border-tint/10 bg-tint/[0.02]">
                    <div className="flex flex-wrap items-center justify-between gap-4 p-5">
                        <div>
                            <p className="text-sm font-semibold text-txt">Number of Articles</p>
                            <p className="text-xs text-muted">Process top N trending articles</p>
                        </div>
                        <div className="flex items-center gap-1.5 rounded-full border border-tint/12 bg-tint/[0.04] p-1">
                            {COUNT_OPTIONS.map((n) => (
                                <button
                                    key={n}
                                    type="button"
                                    onClick={() => setCount(n)}
                                    className={`rounded-full px-4 py-1.5 text-xs font-semibold transition-all ${
                                        count === n
                                            ? 'bg-primary text-onprimary'
                                            : 'text-muted hover:text-txt'
                                    }`}
                                >
                                    {n} article{n > 1 ? 's' : ''}
                                </button>
                            ))}
                        </div>
                    </div>

                    <div className="flex flex-wrap items-center justify-between gap-4 p-5">
                        <div>
                            <p className="text-sm font-semibold text-txt">Auto-Publish</p>
                            <p className="text-xs text-muted">Automatically publish to YouTube</p>
                        </div>
                        <button
                            type="button"
                            role="switch"
                            aria-checked={autoPublish}
                            onClick={() => setAutoPublish((v) => !v)}
                            className={`relative h-6 w-11 shrink-0 rounded-full transition-colors ${
                                autoPublish ? 'bg-accent' : 'bg-tint/25'
                            }`}
                        >
                            <span
                                className={`absolute left-0.5 top-0.5 h-5 w-5 rounded-full shadow transition-transform duration-200 ${
                                    autoPublish ? 'translate-x-5 bg-panel' : 'translate-x-0 bg-txt'
                                }`}
                            />
                        </button>
                    </div>
                </div>

                <button
                    type="button"
                    onClick={handleRun}
                    disabled={isGenerating}
                    className="btn-light w-full py-4 text-base font-semibold disabled:opacity-60"
                >
                    Run Automation
                </button>

                {runNotice && (
                    <p className="mt-3 animate-fadeup rounded-xl border border-tint/15 bg-tint/[0.04] px-4 py-2.5 text-center text-xs text-muted">
                        {runNotice}
                    </p>
                )}

                {/* trending list */}
                <div className="mt-9">
                    <h3 className="mb-4 text-sm font-semibold text-txt">
                        Top Trending Articles{' '}
                        <span className="text-muted">
                            (showing top {visible.length}
                            {articles.length > visible.length ? ` of ${articles.length} ranked` : ''})
                        </span>
                    </h3>

                    {error ? (
                        <div className="rounded-xl border border-red-400/25 bg-red-500/5 p-5 text-center text-sm text-red-300">
                            {error}
                        </div>
                    ) : loading ? (
                        <div className="grid gap-3">
                            {Array.from({ length: 3 }).map((_, i) => (
                                <div key={i} className="h-24 animate-pulse rounded-xl bg-tint/[0.05]" />
                            ))}
                        </div>
                    ) : visible.length === 0 ? (
                        <p className="rounded-xl border border-tint/10 p-6 text-center text-sm text-muted">
                            No fresh trending articles right now.
                        </p>
                    ) : (
                        <div className="grid gap-3">
                            {visible.map((article, i) => (
                                <ArticleRow
                                    key={article.link || i}
                                    article={article}
                                    index={i}
                                    willProcess={i < count}
                                    onGenerate={(a) => onGenerate?.(a, { autoPublish })}
                                    busy={isGenerating}
                                />
                            ))}
                        </div>
                    )}
                </div>
            </div>
        </section>
    );
}
