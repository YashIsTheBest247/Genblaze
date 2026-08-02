import { pipelineSteps } from '../data/options.js';

/* --- stage icons (stroke = currentColor) --- */
const iconProps = {
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 1.6,
    strokeLinecap: 'round',
    strokeLinejoin: 'round',
    viewBox: '0 0 24 24',
};

function ScriptIcon(props) {
    return (
        <svg {...iconProps} {...props}>
            <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
            <path d="M14 3v5h5M8 13h8M8 17h6" />
        </svg>
    );
}

function ImageIcon(props) {
    return (
        <svg {...iconProps} {...props}>
            <rect x="3" y="4" width="18" height="16" rx="2" />
            <circle cx="8.5" cy="9.5" r="1.5" />
            <path d="m4 18 5-5 4 4 3-3 4 4" />
        </svg>
    );
}

function VoiceIcon(props) {
    return (
        <svg {...iconProps} {...props}>
            <rect x="9" y="2" width="6" height="12" rx="3" />
            <path d="M5 11a7 7 0 0 0 14 0M12 18v3" />
        </svg>
    );
}

function SubsIcon(props) {
    return (
        <svg {...iconProps} {...props}>
            <rect x="3" y="5" width="18" height="14" rx="2" />
            <path d="M7 12h4M7 15h7M14 12h3" />
        </svg>
    );
}

function GearIcon(props) {
    return (
        <svg {...iconProps} {...props}>
            <circle cx="12" cy="12" r="3" />
            <path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M19.1 4.9 17 7M7 17l-2.1 2.1" />
        </svg>
    );
}

function PublishIcon(props) {
    return (
        <svg {...iconProps} {...props}>
            <path d="M12 16V4M7 9l5-5 5 5" />
            <path d="M5 16v2a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-2" />
        </svg>
    );
}

function FeedIcon(props) {
    return (
        <svg {...iconProps} {...props}>
            <path d="M4 11a9 9 0 0 1 9 9M4 4a16 16 0 0 1 16 16" />
            <circle cx="5" cy="19" r="1.5" />
        </svg>
    );
}

function TrendIcon(props) {
    return (
        <svg {...iconProps} {...props}>
            <path d="m3 17 6-6 4 4 8-8" />
            <path d="M15 7h6v6" />
        </svg>
    );
}

function ShieldIcon(props) {
    return (
        <svg {...iconProps} {...props}>
            <path d="M12 3 5 6v5.5c0 4.3 2.9 8.3 7 9.5 4.1-1.2 7-5.2 7-9.5V6z" />
            <path d="m9 12 2 2 4-4" />
        </svg>
    );
}

function CloudIcon(props) {
    return (
        <svg {...iconProps} {...props}>
            <path d="M7 18a4 4 0 0 1-.4-8A6 6 0 0 1 18 9.5a3.5 3.5 0 0 1-.5 8z" />
            <path d="M12 12v5M9.5 14.5 12 12l2.5 2.5" />
        </svg>
    );
}

const icons = {
    fetch: FeedIcon,
    trend: TrendIcon,
    script: ScriptIcon,
    image: ImageIcon,
    voice: VoiceIcon,
    subtitles: SubsIcon,
    assembly: GearIcon,
    provenance: ShieldIcon,
    storage: CloudIcon,
    publish: PublishIcon,
};

function statusFor(stepKey, mode, activeStepKey) {
    if (mode === 'complete') return 'done';
    if (mode !== 'running') return 'idle';

    const order = pipelineSteps.map((step) => step.key);
    const activeIndex = order.indexOf(activeStepKey);
    const stepIndex = order.indexOf(stepKey);

    if (stepIndex < activeIndex) return 'done';
    if (stepIndex === activeIndex) return 'active';
    return 'idle';
}

export function PipelineSection({ mode, activeStepKey, statusText }) {
    const steps = pipelineSteps;
    const count = steps.length;
    const activeIndex = steps.findIndex((step) => step.key === activeStepKey);
    const isRunning = mode === 'running';

    // Where the flowing dot sits along the line — follows the active stage.
    const dotIndex =
        mode === 'complete' ? count - 1 : activeIndex >= 0 ? activeIndex : 0;
    const edge = (0.5 / count) * 100; // line inset so it meets icon centers
    const dotLeft = ((dotIndex + 0.5) / count) * 100;

    return (
        <section id="pipeline" className="mx-auto max-w-7xl scroll-mt-20 px-4 py-10 sm:px-5 sm:py-12 lg:px-8">
            <div className="mb-6 flex flex-wrap items-end justify-between gap-3 sm:mb-10 sm:gap-4">
                <div>
                    <span className="eyebrow">Live render</span>
                    <h2 className="display mt-2 text-2xl font-semibold sm:text-4xl">Pipeline</h2>
                </div>
                <span className="chip">
                    <span
                        className={`h-1.5 w-1.5 rounded-full ${
                            isRunning
                                ? 'animate-pulse bg-accent'
                                : mode === 'complete'
                                  ? 'bg-accent'
                                  : mode === 'error'
                                    ? 'bg-red-400'
                                    : 'bg-faint'
                        }`}
                    />
                    {statusText}
                </span>
            </div>

            {/* Ten stages will never fit a phone, so the strip scrolls horizontally
                inside its own container and the page body never moves sideways.
                min-width was 680px when there were eight stages; the two added
                (Provenance, Backblaze B2) were cramming the icons. */}
            <div className="glass overflow-x-auto p-5 sm:p-8 lg:p-12">
                <div className="relative min-w-[860px]">
                    {/* dashed base line */}
                    <div
                        className="absolute top-7 border-t border-dashed border-tint/15"
                        style={{ left: `${edge}%`, right: `${edge}%` }}
                    />
                    {/* progress fill up to the dot */}
                    <div
                        className="absolute top-7 border-t-2 border-accent/70 transition-all duration-700 ease-out"
                        style={{ left: `${edge}%`, width: `${Math.max(0, dotLeft - edge)}%` }}
                    />

                    {/* stages */}
                    <ol
                        className="relative grid"
                        style={{ gridTemplateColumns: `repeat(${count}, minmax(0, 1fr))` }}
                    >
                        {steps.map((step) => {
                            const status = statusFor(step.key, mode, activeStepKey);
                            const active = status === 'active';
                            const done = status === 'done';
                            const Icon = icons[step.key] ?? ScriptIcon;
                            const isWheel = step.key === 'assembly';

                            return (
                                <li key={step.key} className="flex flex-col items-center gap-3 px-1">
                                    <span className="relative grid h-14 w-14 place-items-center">
                                        {/* spinning dashed ring on the active stage */}
                                        {active && (
                                            <>
                                                <span className="absolute inset-0 animate-spin rounded-full border-[2.5px] border-dashed border-accent [animation-duration:3s]" />
                                                <span className="absolute -inset-1.5 animate-ping rounded-full border border-accent/30" />
                                            </>
                                        )}
                                        <span
                                            className={`grid h-12 w-12 place-items-center rounded-full border bg-panel transition-all duration-300 ${
                                                active
                                                    ? 'border-accent/40 text-accent shadow-glow'
                                                    : done
                                                      ? 'border-accent/50 text-accent'
                                                      : 'border-tint/12 text-muted'
                                            }`}
                                        >
                                            <Icon
                                                className={`h-5 w-5 ${
                                                    isWheel
                                                        ? `animate-spin ${active ? '[animation-duration:1.5s]' : '[animation-duration:7s]'}`
                                                        : ''
                                                }`}
                                            />
                                        </span>
                                    </span>
                                    <span
                                        className={`text-xs font-medium ${
                                            active ? 'text-txt' : done ? 'text-accent' : 'text-muted'
                                        }`}
                                    >
                                        {step.label}
                                    </span>
                                </li>
                            );
                        })}
                    </ol>
                </div>
            </div>
        </section>
    );
}
