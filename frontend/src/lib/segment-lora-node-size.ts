interface SizedNode {
  size: [number, number]
  setSize?: (size: [number, number]) => void
  setDirtyCanvas?: (foreground: boolean, background: boolean) => void
  onNodeCreated?: () => void
}
export function initializeSegmentLoraSize(nodeType: { prototype: object }, nodeData: { name?: string }): void {
  if (nodeData.name !== 'easy h3SegmentLoras') return
  const prototype = nodeType.prototype as SizedNode
  const original = prototype.onNodeCreated
  prototype.onNodeCreated = function () {
    original?.call(this)
    if (this.setSize) this.setSize([640, 430])
    else this.size = [640, 430]
    this.setDirtyCanvas?.(true, true)
  }
}
