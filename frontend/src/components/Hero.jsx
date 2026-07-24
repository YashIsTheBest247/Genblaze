import { useState } from 'react';
import { pipelineSteps } from '../data/options.js';

// Drop your demo clip at frontend/public/demo.mp4 (or set VITE_DEMO_VIDEO_URL).
const demoUrl = import.meta.env.VITE_DEMO_VIDEO_URL?.trim() || '/demo.mp4';

function StageIcon({ className }) {
    return (
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" className={className}>
            <rect x="3" y="4" width="18" height="16" rx="2" />
            <path d="M7 9h10M7 13h6" />
        </svg>
    );
}

export function Hero({ onStartAutomation, onViewLibrary }) {
    const [demoFailed, setDemoFailed] = useState(false);

    return (
        <section className="mx-auto w-full max-w-[1600px] px-5 pt-12 lg:px-8">
            <div className="grid items-center gap-10 lg:grid-cols-2">
                {/* Copy */}
                <div>
                    <h1 className="display text-5xl font-bold leading-[1.05] tracking-tight sm:text-6xl lg:text-7xl">
                        Economic Times.
                        <br />
                        <span className="text-muted">AI Powered.</span>
                    </h1>

                    <p className="mt-6 max-w-xl text-base leading-relaxed text-txt/70">
                        AI system that automatically converts trending Economic Times articles into
                        viral short-form videos. From news to video in minutes.
                    </p>

                    <div className="mt-9 flex flex-wrap items-center gap-3">
                        <button type="button" onClick={onStartAutomation} className="btn-light px-7 py-3.5 text-sm font-semibold">
                            Start Automation
                            <span aria-hidden className="ml-1">→</span>
                        </button>
                        <button type="button" onClick={onViewLibrary} className="btn-ghost px-7 py-3.5 text-sm font-semibold">
                            View Library
                        </button>
                    </div>
                </div>

                {/* Demo video */}
                <div className="glass relative overflow-hidden rounded-2xl border-tint/12 p-3 lg:p-4">
                    <div className="pointer-events-none absolute -right-16 -top-16 h-64 w-64 rounded-full bg-tint/[0.05] blur-3xl" />
                    {demoFailed ? (
                        <div className="relative grid aspect-video place-items-center rounded-xl border border-dashed border-tint/20 bg-black/40 p-6 text-center">
                            <div>
                                <p className="text-sm font-semibold text-txt">No demo video yet</p>
                                <p className="mt-1.5 text-xs text-muted">
                                    Add your clip at{' '}
                                    <code className="rounded bg-tint/10 px-1.5 py-0.5 text-[0.7rem]">
                                        frontend/public/demo.mp4
                                    </code>
                                </p>
                            </div>
                        </div>
                    ) : (
                        <video
                            src={demoUrl}
                            controls
                            autoPlay
                            muted
                            loop
                            playsInline
                            onError={() => setDemoFailed(true)}
                            className="relative aspect-video w-full rounded-xl bg-black object-cover"
                        />
                    )}
                </div>
            </div>

            {/* Automation pipeline stage strip */}
            <div className="mt-14">
                <div className="mb-5 flex items-center justify-between">
                    <span className="eyebrow">Automation pipeline</span>
                    <span className="text-[0.65rem] font-semibold uppercase tracking-[0.16em] text-muted">
                        Library synced
                    </span>
                </div>
                <div className="grid gap-x-6 gap-y-4 sm:grid-cols-3 lg:grid-cols-5">
                    {pipelineSteps.map((step) => (
                        <div key={step.key} className="border-t border-tint/12 pt-3">
                            <div className="flex items-center gap-2 text-muted">
                                <StageIcon className="h-4 w-4" />
                                <span className="text-xs font-semibold uppercase tracking-[0.12em]">
                                    {step.label}
                                </span>
                            </div>
                        </div>
                    ))}
                </div>
            </div>
        </section>
    );
}
