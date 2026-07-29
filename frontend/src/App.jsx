import { useCallback, useEffect, useRef, useState } from 'react';
import { Header } from './components/Header.jsx';
import { Hero } from './components/Hero.jsx';
import { AutomationSection } from './components/AutomationSection.jsx';
import { PipelineSection } from './components/PipelineSection.jsx';
import { LibrarySection } from './components/LibrarySection.jsx';
import { DevFooter } from './components/DevFooter.jsx';
import { ConfirmDialog } from './components/ConfirmDialog.jsx';
import { VideoPlayerModal } from './components/VideoPlayerModal.jsx';
import { ProvenanceModal } from './components/ProvenanceModal.jsx';
import { pipelineSteps } from './data/options.js';
import { buildVideoPayload } from './lib/payload.js';
import {
    generateVideo,
    listVideos,
    deleteVideo,
    getRenderStatus,
    getStorageStatus,
} from './api/videos.js';
import { runAutomation } from './api/trends.js';

const idlePipeline = {
    mode: 'idle',
    activeStepKey: null,
    statusText: 'Ready to render',
};

export function App() {
    const [videos, setVideos] = useState([]);
    const [isGenerating, setIsGenerating] = useState(false);
    const [isPolling, setIsPolling] = useState(false);
    const [pipeline, setPipeline] = useState(idlePipeline);
    const [searchQuery, setSearchQuery] = useState('');
    const [newFilename, setNewFilename] = useState(null);
    const [pendingDelete, setPendingDelete] = useState(null);
    const [playingVideo, setPlayingVideo] = useState(null);
    const [provenanceVideo, setProvenanceVideo] = useState(null);
    const [storageStatus, setStorageStatus] = useState(null);

    const pollTimerRef = useRef(null);
    const generatingRef = useRef(false);

    const refreshLibrary = useCallback(async () => {
        try {
            const response = await listVideos();
            setVideos(response);
            return response;
        } catch {
            setVideos([]);
            return [];
        }
    }, []);

    useEffect(() => {
        // Where media actually lives (B2 bucket) and whether Genblaze is active.
        getStorageStatus().then(setStorageStatus).catch(() => setStorageStatus(null));
    }, []);

    useEffect(() => {
        (async () => {
            const current = await refreshLibrary();
            // If a render is already running server-side (e.g. the page was
            // refreshed, or the scheduler fired), re-attach to it.
            try {
                const status = await getRenderStatus();
                if (status?.active) {
                    const resumed = pipelineFromStage(status.stage);
                    if (resumed) setPipeline(resumed);
                    startRenderWatch(new Set(current.map((video) => video.name)));
                }
            } catch {
                // backend not reachable yet — ignore
            }
        })();
        return () => {
            if (pollTimerRef.current) window.clearInterval(pollTimerRef.current);
        };
    }, [refreshLibrary]);

    function clearPollTimer() {
        if (pollTimerRef.current) {
            window.clearInterval(pollTimerRef.current);
            pollTimerRef.current = null;
        }
    }

    function completePipeline() {
        clearPollTimer();
        setIsPolling(false);
        setPipeline({ mode: 'complete', activeStepKey: null, statusText: 'Video ready' });
        scrollToSection('library');
    }

    // Map a backend stage key onto the visual pipeline state.
    function pipelineFromStage(stage) {
        if (!stage || stage === 'done') return null;
        const step = pipelineSteps.find((s) => s.key === stage);
        if (!step) return null;
        return { mode: 'running', activeStepKey: step.key, statusText: step.statusText };
    }

    /**
     * Watch a render: reflect the REAL backend stage in the pipeline, and finish
     * when a video that wasn't in `baselineNames` shows up in the library.
     */
    function startRenderWatch(baselineNames, maxAttempts = 240) {
        clearPollTimer();
        setIsPolling(true);
        let attempts = 0;
        let idleTicks = 0; // consecutive polls where the backend reports nothing running

        function stopWatch(nextPipeline) {
            clearPollTimer();
            setIsPolling(false);
            setPipeline(nextPipeline);
        }

        pollTimerRef.current = window.setInterval(async () => {
            attempts += 1;

            let status = null;
            try {
                status = await getRenderStatus();
                if (status?.stage === 'error') {
                    stopWatch({
                        mode: 'error',
                        activeStepKey: null,
                        statusText: status.error || 'Render failed',
                    });
                    return;
                }
                const next = pipelineFromStage(status?.stage);
                if (next) setPipeline(next);
            } catch {
                // transient status error — keep watching
            }

            const refreshed = await refreshLibrary();
            const fresh = refreshed.find((video) => !baselineNames.has(video.name));

            // Finish only once the backend reports the WHOLE render is done —
            // 'done' is set after the publish step, so we don't jump to the
            // library while the YouTube upload is still running.
            if (status?.stage === 'done' || (fresh && status?.active === false)) {
                if (fresh) setNewFilename(fresh.name);
                completePipeline();
                return;
            }

            // Nothing is running server-side and no new video arrived. This happens
            // if the backend restarted mid-render (status is in-memory) or the run
            // ended without output — don't spin for minutes, bail out with a reason.
            const backendIdle = status && status.active === false && !status.stage;
            idleTicks = backendIdle ? idleTicks + 1 : 0;
            if (idleTicks >= 3) {
                stopWatch({
                    mode: 'error',
                    activeStepKey: null,
                    statusText: 'Render stopped — no video was produced',
                });
                return;
            }

            if (attempts >= maxAttempts) {
                stopWatch({
                    mode: 'error',
                    activeStepKey: null,
                    statusText: 'Timed out waiting for the render',
                });
            }
        }, 2500);
    }

    async function confirmDelete() {
        const target = pendingDelete;
        setPendingDelete(null);
        if (!target) return;

        // Drop the card immediately rather than waiting on the round trip. The
        // B2 listing stays authoritative — the refresh below reconciles, so a
        // failed delete puts the card straight back.
        setVideos((current) => current.filter((video) => video.name !== target.name));

        try {
            await deleteVideo(target.name);
        } catch (error) {
            // A 404 means it is already gone, which is the outcome we wanted.
            const alreadyGone = /not found/i.test(error?.message || '');
            if (!alreadyGone) {
                console.error('Failed to delete video:', error);
                window.alert(error.message || 'Could not delete the video.');
            }
        } finally {
            await refreshLibrary();
        }
    }

    function handleGenerateFromArticle(article) {
        handleGenerate({
            topic: article.title,
            duration: 60,
            keyPoints: (article.keywords ?? []).join(', '),
            autoPublish: true,
            privacy: 'unlisted',
        }).catch((error) => {
            window.alert(error.message || 'Could not start generation.');
        });
    }

    /** Shared setup for any render trigger: scroll to pipeline, guard, baseline. */
    async function beginRender() {
        // Always take the user to the live pipeline (even if one is running).
        scrollToSection('pipeline');

        if (generatingRef.current || isGenerating || isPolling) {
            throw new Error('A render is already in progress. Please wait for it to finish.');
        }
        generatingRef.current = true;
        setIsGenerating(true);
        setNewFilename(null);
        setPipeline({ mode: 'running', activeStepKey: 'fetch', statusText: 'Starting render…' });

        const current = await refreshLibrary();
        return new Set(current.map((video) => video.name));
    }

    function failRender(message) {
        setPipeline({ mode: 'error', activeStepKey: null, statusText: message });
    }

    async function handleGenerate(formValues) {
        const baseline = await beginRender();
        try {
            const response = await generateVideo(buildVideoPayload(formValues));
            if (response?.success === false) {
                throw new Error(response.error || response.message || 'Generation failed.');
            }
            startRenderWatch(baseline);
        } catch (error) {
            failRender('Render request failed');
            throw error;
        } finally {
            setIsGenerating(false);
            generatingRef.current = false;
        }
    }

    /** "Run Automation": backend picks the top-N trending articles and renders them. */
    async function handleRunAutomation({ topN = 1, autoPublish = false } = {}) {
        const baseline = await beginRender();
        try {
            await runAutomation({ topN, autoPublish });
            startRenderWatch(baseline);
        } catch (error) {
            failRender('Automation request failed');
            throw error;
        } finally {
            setIsGenerating(false);
            generatingRef.current = false;
        }
    }

    function scrollToSection(id) {
        if (id === 'top') {
            window.scrollTo({ top: 0, behavior: 'smooth' });
            return;
        }
        document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }

    return (
        <div className="flex min-h-screen flex-col">
            <Header
                onNavigate={scrollToSection}
                searchQuery={searchQuery}
                onSearchChange={setSearchQuery}
            />
            <main className="flex-1">
                <Hero
                    onStartAutomation={() => scrollToSection('automation')}
                    onViewLibrary={() => scrollToSection('library')}
                />
                <AutomationSection
                    onGenerate={handleGenerateFromArticle}
                    onRunAutomation={handleRunAutomation}
                    isGenerating={isGenerating || isPolling}
                />
                <PipelineSection
                    mode={pipeline.mode}
                    activeStepKey={pipeline.activeStepKey}
                    statusText={pipeline.statusText}
                />
                <LibrarySection
                    videos={videos}
                    searchQuery={searchQuery}
                    isGenerating={isGenerating}
                    isPolling={isPolling}
                    newFilename={newFilename}
                    onRequestDelete={setPendingDelete}
                    onPlay={setPlayingVideo}
                    onShowProvenance={setProvenanceVideo}
                    storageStatus={storageStatus}
                />
            </main>
            <DevFooter />
            <ConfirmDialog
                open={Boolean(pendingDelete)}
                title="Delete this render?"
                message={
                    pendingDelete
                        ? `"${pendingDelete.title}" will be permanently removed from the library.`
                        : ''
                }
                confirmLabel="Delete"
                onConfirm={confirmDelete}
                onCancel={() => setPendingDelete(null)}
            />
            <VideoPlayerModal video={playingVideo} onClose={() => setPlayingVideo(null)} />
            <ProvenanceModal video={provenanceVideo} onClose={() => setProvenanceVideo(null)} />
        </div>
    );
}
