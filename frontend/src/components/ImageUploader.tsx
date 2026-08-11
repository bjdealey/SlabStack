import { useRef, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ImagePlus, Star, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import { api, ApiError, keys } from '@/lib/api'
import type { Card, CardImage } from '@/lib/types'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

/**
 * Front and back are separate slots on purpose: a back photograph is what makes
 * back centering and edge whitening checkable, and those decide grades.
 */
export function ImageUploader({ card }: { card: Card }) {
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      <ImageSlot card={card} side="front" label="Front" />
      <ImageSlot card={card} side="back" label="Back" />
    </div>
  )
}

function ImageSlot({ card, side, label }: { card: Card; side: 'front' | 'back'; label: string }) {
  const queryClient = useQueryClient()
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)

  const images = card.images.filter((image) => image.side === side)
  const primary = images.find((image) => image.is_primary) ?? images[0]

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: keys.card(card.id) })
    queryClient.invalidateQueries({ queryKey: keys.evaluation(card.id) })
    queryClient.invalidateQueries({ queryKey: keys.summary })
  }

  const upload = useMutation({
    mutationFn: (files: File[]) => api.uploadImages(card.id, files, side),
    onSuccess: () => {
      toast.success(`${label} image uploaded`)
      invalidate()
    },
    onError: (error: unknown) =>
      toast.error(error instanceof ApiError ? error.message : 'Upload failed'),
  })

  const remove = useMutation({
    mutationFn: (imageId: string) => api.deleteImage(imageId),
    onSuccess: invalidate,
  })

  const promote = useMutation({
    mutationFn: (imageId: string) => api.updateImage(imageId, { is_primary: true }),
    onSuccess: invalidate,
  })

  const handleFiles = (fileList: FileList | null) => {
    const files = Array.from(fileList ?? [])
    if (files.length) upload.mutate(files)
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <p className="text-xs font-medium uppercase tracking-wider text-ink-faint">{label}</p>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => inputRef.current?.click()}
          disabled={upload.isPending}
        >
          <ImagePlus /> {upload.isPending ? 'Uploading…' : 'Add'}
        </Button>
      </div>

      <div
        onDragOver={(event) => {
          event.preventDefault()
          setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault()
          setDragging(false)
          handleFiles(event.dataTransfer.files)
        }}
        onClick={() => !primary && inputRef.current?.click()}
        className={cn(
          'relative flex aspect-[5/7] items-center justify-center overflow-hidden rounded-[var(--radius-card)] border border-dashed border-line bg-canvas transition-colors',
          dragging && 'border-brand bg-brand/5',
          !primary && 'cursor-pointer hover:border-ink-faint',
        )}
      >
        {primary ? (
          <img
            src={primary.url}
            alt={`${card.name} ${side}`}
            className="size-full object-contain"
            loading="lazy"
          />
        ) : (
          <div className="px-4 text-center text-xs text-ink-faint">
            <ImagePlus className="mx-auto mb-2 size-6" />
            Drop a {label.toLowerCase()} photo here
          </div>
        )}
      </div>

      <input
        ref={inputRef}
        type="file"
        accept="image/jpeg,image/png,image/webp"
        multiple
        className="hidden"
        onChange={(event) => {
          handleFiles(event.target.files)
          event.target.value = ''
        }}
      />

      {images.length > 1 ? (
        <div className="flex flex-wrap gap-2">
          {images.map((image) => (
            <Thumb
              key={image.id}
              image={image}
              onPromote={() => promote.mutate(image.id)}
              onDelete={() => remove.mutate(image.id)}
            />
          ))}
        </div>
      ) : images.length === 1 ? (
        <Button
          size="sm"
          variant="ghost"
          className="text-negative"
          onClick={() => remove.mutate(images[0].id)}
        >
          <Trash2 /> Remove
        </Button>
      ) : null}
    </div>
  )
}

function Thumb({
  image,
  onPromote,
  onDelete,
}: {
  image: CardImage
  onPromote: () => void
  onDelete: () => void
}) {
  return (
    <div
      className={cn(
        'group relative size-16 overflow-hidden rounded-md border',
        image.is_primary ? 'border-brand' : 'border-line',
      )}
    >
      <img
        src={image.thumbnail_url ?? image.url}
        alt=""
        className="size-full object-cover"
        loading="lazy"
      />
      <div className="absolute inset-0 hidden items-center justify-center gap-1 bg-black/70 group-hover:flex">
        {!image.is_primary ? (
          <button onClick={onPromote} title="Make primary" className="text-caution">
            <Star className="size-4" />
          </button>
        ) : null}
        <button onClick={onDelete} title="Delete" className="text-negative">
          <Trash2 className="size-4" />
        </button>
      </div>
    </div>
  )
}
