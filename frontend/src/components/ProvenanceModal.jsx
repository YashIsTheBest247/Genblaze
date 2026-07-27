import { useEffect, useState } from 'react';
import { getProvenance } from '../api/videos.js';
import { titleFromFilename } from '../lib/payload.js';

function short(hash, head = 10, tail = 6) {
    if (!hash) return '—';
    return hash.length <= head + tail + 1 ? hash : `${hash.slice(0, head)}…${hash.slice(-tail)}`;
}

function bytes(size) {
    if (!size && size !== 0) return null;
    const units = ['B', 'KB', 'MB', 'GB'];
    let value = size;
    let unit = 0;
    while (value >= 1024 && unit < units.length - 1) {
        value /= 1024;
        unit += 1;
    }
    return `${value.toFixed(value < 10 && unit > 0 ? 1 : 0)} ${units[unit]}`;
}

function CopyButton({ value }) {
    const [copied, setCopied] = useState(false);
    if (!value) return null;
    return (
        <button
            type="button"
            onClick={async () => {
                try {
                    await navigator.clipboard.writeText(value);
                    setCopied(true);
                    window.setTimeout(() => setCopied(false), 1400);
                } catch {
                    /* clipboard unavailable — the value is visible anyway */
                }
            }}
            className="rounded border border-tint/15 px-1.5 py-0.5 text-[0.6rem] font-semibold uppercase tracking-wider text-muted transition-colors hover:border-accent/40 hover:text-accent"
        >
            {copied ? 'Copied' : 'Copy'}
        </button>
    );
}

function Row({ label, children }) {
    return (
        <div className="flex items-start justify-between gap-4 border-b border-tint/10 py-2.5 last:border-0">
            <span className="shrink-0 text-[0.65rem] font-semibold uppercase tracking-[0.12em] text-muted">
                {label}
            </span>
            <span className="min-w-0 text-right text-xs text-txt">{children}</span>
        </div>
    );
}

/**
 * Provenance viewer — answers "how was this media made?" from the Genblaze
 * manifest: the provider/model behind every stage, the SHA-256 of every
 * artefact, and whether the manifest still verifies against them.
 */
export function ProvenanceModal({ video, onClose }) {
    const [data, setData] = useState(null);
    const [error, setError] = useState(null);
    const [loading, setLoading] = useState(true);
    const [rawOpen, setRawOpen] = useState(false);

    useEffect(() => {
        if (!video?.name) return undefined;
        let cancelled = false;

        setLoading(true);
        setError(null);
        getProvenance(video.name)
            .then((response) => {
                if (!cancelled) setData(response);
            })
            .catch((e) => {
                if (!cancelled) setError(e.message || 'Could not load provenance.');
            })
            .finally(() => {
                if (!cancelled) setLoading(false);
            });

        return () => {
            cancelled = true;
        };
    }, [video?.name]);

    useEffect(() => {
        const onKey = (e) => e.key === 'Escape' && onClose?.();
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
    }, [onClose]);

    if (!video) return null;

    const verified = data?.verified;

    return (
        <div
            className="fixed inset-0 z-50 grid place-items-center bg-black/70 p-4 backdrop-blur-sm"
            onClick={onClose}
            role="presentation"
        >
            <div
                className="max-h-[88vh] w-full max-w-2xl animate-popIn overflow-y-auto rounded-2xl border border-tint/15 bg-panel p-6 shadow-card"
                onClick={(e) => e.stopPropagation()}
            >
                <div className="flex items-start justify-between gap-4">
                    <div className="min-w-0">
                        <span className="eyebrow">Genblaze provenance</span>
                        <h3 className="display mt-1 truncate text-xl font-semibold text-txt">
                            {titleFromFilename(video.name)}
                        </h3>
                    </div>
                    <button
                        type="button"
                        onClick={onClose}
                        aria-label="Close"
                        className="grid h-8 w-8 shrink-0 place-items-center rounded-lg text-muted transition-colors hover:bg-tint/[0.08] hover:text-txt"
                    >
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" className="h-4 w-4">
                            <path d="M6 6l12 12M18 6 6 18" />
                        </svg>
                    </button>
                </div>

                {loading && <p className="mt-6 text-sm text-muted">Loading manifest…</p>}
                {error && <p className="mt-6 text-sm text-red-400">{error}</p>}

                {!loading && !error && data && !data.available && (
                    <div className="mt-6 rounded-xl border border-tint/10 p-6 text-center">
                        <p className="text-sm font-semibold text-txt">No manifest for this video</p>
                        <p className="mt-1 text-xs text-muted">
                            It was rendered before provenance recording was enabled.
                        </p>
                    </div>
                )}

                {!loading && !error && data?.available && (
                    <>
                        {/* verification banner */}
                        <div
                            className={`mt-5 flex items-center gap-3 rounded-xl border p-4 ${
                                verified
                                    ? 'border-accent/40 bg-accent/[0.07]'
                                    : 'border-amber-500/40 bg-amber-500/[0.07]'
                            }`}
                        >
                            <span className={verified ? 'text-accent' : 'text-amber-400'}>
                                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" className="h-6 w-6" strokeLinecap="round" strokeLinejoin="round">
                                    <path d="M12 3 5 6v5.5c0 4.3 2.9 8.3 7 9.5 4.1-1.2 7-5.2 7-9.5V6z" />
                                    {verified ? <path d="m9 12 2 2 4-4" /> : <path d="M12 8v5M12 16h.01" />}
                                </svg>
                            </span>
                            <div className="min-w-0">
                                <p className="text-sm font-semibold text-txt">
                                    {verified ? 'Manifest verified' : 'Manifest not fully verified'}
                                </p>
                                <p className="mt-0.5 text-xs text-muted">
                                    {verified
                                        ? 'Canonical hash and every artefact SHA-256 check out.'
                                        : data.verification?.reason ||
                                          'The hash chain could not be fully confirmed.'}
                                </p>
                            </div>
                        </div>

                        {/* run identity */}
                        <div className="mt-5">
                            <Row label="Canonical hash">
                                <span className="inline-flex items-center gap-2">
                                    <code className="font-mono text-[0.7rem] text-txt">
                                        {short(data.canonical_hash, 14, 8)}
                                    </code>
                                    <CopyButton value={data.canonical_hash} />
                                </span>
                            </Row>
                            <Row label="Run ID">
                                <code className="font-mono text-[0.7rem]">{short(data.run_id, 12, 6)}</code>
                            </Row>
                            <Row label="Tenant">{data.tenant_id || '—'}</Row>
                            <Row label="Recorded">
                                {data.created_at ? new Date(data.created_at).toLocaleString() : '—'}
                            </Row>
                            <Row label="Stored on">
                                {video.storage === 'b2' ? 'Backblaze B2' : 'Local disk'}
                            </Row>
                        </div>

                        {/* generation chain */}
                        {data.stages?.length > 0 && (
                            <div className="mt-6">
                                <p className="eyebrow">Generation chain</p>
                                <ol className="mt-3 space-y-2">
                                    {data.stages.map((stage, index) => (
                                        <li
                                            key={`${stage.stage}-${index}`}
                                            className="rounded-lg border border-tint/10 bg-tint/[0.03] px-3 py-2.5"
                                        >
                                            <div className="flex items-center justify-between gap-3">
                                                <span className="text-xs font-semibold capitalize text-txt">
                                                    {index + 1}. {stage.stage}
                                                </span>
                                                {stage.provider && (
                                                    <span className="rounded bg-accent/15 px-2 py-0.5 text-[0.6rem] font-bold uppercase tracking-wider text-accent">
                                                        {stage.provider}
                                                    </span>
                                                )}
                                            </div>
                                            {stage.model && (
                                                <p className="mt-1 font-mono text-[0.68rem] text-muted">
                                                    {stage.model}
                                                </p>
                                            )}
                                            {stage.genblaze_run_id && (
                                                <p className="mt-1 text-[0.62rem] text-faint">
                                                    sub-run {short(stage.genblaze_run_id, 8, 4)}
                                                </p>
                                            )}
                                        </li>
                                    ))}
                                </ol>
                            </div>
                        )}

                        {/* artefacts */}
                        {data.assets?.length > 0 && (
                            <div className="mt-6">
                                <p className="eyebrow">Artefacts on Backblaze B2</p>
                                <ul className="mt-3 space-y-2">
                                    {data.assets.map((asset) => (
                                        <li
                                            key={asset.asset_id}
                                            className="rounded-lg border border-tint/10 px-3 py-2.5"
                                        >
                                            <div className="flex items-center justify-between gap-3">
                                                <span className="truncate text-xs font-semibold text-txt">
                                                    {asset.filename || asset.role}
                                                </span>
                                                <span className="shrink-0 text-[0.62rem] uppercase tracking-wider text-muted">
                                                    {asset.role}
                                                    {bytes(asset.size_bytes) ? ` · ${bytes(asset.size_bytes)}` : ''}
                                                </span>
                                            </div>
                                            <div className="mt-1 flex items-center gap-2">
                                                <code className="truncate font-mono text-[0.65rem] text-faint">
                                                    sha256 {short(asset.sha256, 16, 8)}
                                                </code>
                                                <CopyButton value={asset.sha256} />
                                            </div>
                                        </li>
                                    ))}
                                </ul>
                            </div>
                        )}

                        {/* raw manifest */}
                        <div className="mt-6">
                            <button
                                type="button"
                                onClick={() => setRawOpen((v) => !v)}
                                className="text-xs font-semibold text-accent transition-opacity hover:opacity-80"
                            >
                                {rawOpen ? 'Hide' : 'Show'} raw manifest JSON
                            </button>
                            {rawOpen && (
                                <pre className="mt-3 max-h-64 overflow-auto rounded-lg border border-tint/10 bg-black/40 p-3 font-mono text-[0.62rem] leading-relaxed text-muted">
                                    {JSON.stringify(data.manifest, null, 2)}
                                </pre>
                            )}
                        </div>
                    </>
                )}
            </div>
        </div>
    );
}
