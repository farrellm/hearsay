import { imageUrl } from '@/api/client'

/** A show's or episode's cached artwork, or a plain square while there's none. */
export function Artwork({ sha, size }: { sha: string | null; size: 48 | 96 }) {
  const style = { width: size, height: size }
  if (sha === null) return <span className="artwork artwork-none" style={style} aria-hidden />
  return (
    <img
      className="artwork"
      style={style}
      src={imageUrl(sha, size > 64 ? 512 : 128)}
      alt=""
      loading="lazy"
      decoding="async"
    />
  )
}
