import { useCallback, useEffect, useRef, useState } from 'react'

/** Keep the silent bridge overlay aligned with the original clips' playback clock. */
export function useBridgePreview(url: string | null, time: number, playing: boolean) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const [failedUrl, setFailedUrl] = useState<string | null>(null)
  const sync = useCallback(() => {
    const video = videoRef.current
    if (!video || !url || failedUrl === url) return
    try {
      if (!playing || video.paused || Math.abs(video.currentTime - time) > 0.12) video.currentTime = time
      if (playing) {
        if (video.paused) void video.play()?.catch((error: unknown) => {
          console.error('[ProjectVideoCombineWidget] failed to play bridge preview:', error)
          setFailedUrl(url)
        })
      } else video.pause()
    } catch (error) {
      console.error('[ProjectVideoCombineWidget] failed to seek bridge preview:', error)
      setFailedUrl(url)
    }
  }, [failedUrl, playing, time, url])

  useEffect(sync, [sync])
  useEffect(() => {
    const video = videoRef.current
    return () => video?.pause()
  }, [url])

  const onError = useCallback(() => {
    console.error('[ProjectVideoCombineWidget] bridge preview unavailable:', videoRef.current?.error)
    setFailedUrl(url)
  }, [url])
  return { videoRef, sync, onError, failed: url !== null && failedUrl === url }
}
