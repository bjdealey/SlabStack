import * as React from 'react'
import * as LabelPrimitive from '@radix-ui/react-label'
import { cn } from '@/lib/utils'

export const Label = React.forwardRef<
  React.ComponentRef<typeof LabelPrimitive.Root>,
  React.ComponentPropsWithoutRef<typeof LabelPrimitive.Root>
>(({ className, ...props }, ref) => (
  <LabelPrimitive.Root
    ref={ref}
    className={cn('text-xs font-medium text-ink-muted', className)}
    {...props}
  />
))
Label.displayName = 'Label'

const controlClasses =
  'w-full rounded-lg border border-line bg-canvas px-3 py-2 text-sm text-ink placeholder:text-ink-faint focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand disabled:opacity-50'

export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...props }, ref) => (
    <input ref={ref} className={cn(controlClasses, 'h-9 py-0', className)} {...props} />
  ),
)
Input.displayName = 'Input'

export const Textarea = React.forwardRef<
  HTMLTextAreaElement,
  React.TextareaHTMLAttributes<HTMLTextAreaElement>
>(({ className, ...props }, ref) => (
  <textarea ref={ref} className={cn(controlClasses, 'min-h-20', className)} {...props} />
))
Textarea.displayName = 'Textarea'

/**
 * A native select. Radix Select is lovely but overkill for a form with 15
 * dropdowns — this keeps keyboard behaviour and adds nothing to the bundle.
 */
export const Select = React.forwardRef<
  HTMLSelectElement,
  React.SelectHTMLAttributes<HTMLSelectElement>
>(({ className, children, ...props }, ref) => (
  <select ref={ref} className={cn(controlClasses, 'h-9 py-0 pr-8', className)} {...props}>
    {children}
  </select>
))
Select.displayName = 'Select'

/**
 * A labelled control.
 *
 * The label is tied to its control with a generated id rather than left as a
 * sibling: an unassociated label is not announced by a screen reader and does
 * not focus the field when clicked. The id is threaded onto the single element
 * child, so callers write `<Field label="…"><Input /></Field>` and get the
 * association for free.
 */
export function Field({
  label,
  hint,
  error,
  className,
  children,
}: {
  label?: string
  hint?: string
  error?: string
  className?: string
  children: React.ReactNode
}) {
  const generated = React.useId()
  let controlId: string | undefined

  const control = React.Children.map(children, (child) => {
    if (!React.isValidElement(child) || controlId !== undefined) return child
    const props = child.props as { id?: string }
    controlId = props.id ?? generated
    return props.id ? child : React.cloneElement(child, { id: controlId } as never)
  })

  return (
    <div className={cn('space-y-1.5', className)}>
      {label ? <Label htmlFor={controlId}>{label}</Label> : null}
      {control}
      {error ? (
        <p className="text-xs text-negative">{error}</p>
      ) : hint ? (
        <p className="text-xs text-ink-faint">{hint}</p>
      ) : null}
    </div>
  )
}
