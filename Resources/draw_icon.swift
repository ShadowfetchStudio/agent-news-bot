import AppKit

// Original Agent News Bot artwork. Deterministic native drawing, no external fonts/assets.
let size = 1024
let output = CommandLine.arguments.dropFirst().first ?? "icon-1024.png"
let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: size, pixelsHigh: size,
    bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
    colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
NSGraphicsContext.current?.imageInterpolation = .high
NSColor.clear.setFill()
NSRect(x: 0, y: 0, width: size, height: size).fill()
let navy = NSColor(red: 0.035, green: 0.055, blue: 0.09, alpha: 1)
let slate = NSColor(red: 0.12, green: 0.18, blue: 0.25, alpha: 1)
let gold = NSColor(red: 0.93, green: 0.74, blue: 0.39, alpha: 1)
let aqua = NSColor(red: 0.37, green: 0.83, blue: 0.86, alpha: 1)
let tile = NSBezierPath(roundedRect: NSRect(x: 40, y: 40, width: 944, height: 944), xRadius: 208, yRadius: 208)
NSGradient(starting: slate, ending: navy)!.draw(in: tile, angle: -70)
NSColor.white.withAlphaComponent(0.12).setStroke(); tile.lineWidth = 3; tile.stroke()

func stroke(_ path: NSBezierPath, color: NSColor, width: CGFloat) {
    color.setStroke(); path.lineWidth = width; path.lineCapStyle = .round; path.lineJoinStyle = .round; path.stroke()
}
func line(_ x: CGFloat, _ y: CGFloat, _ width: CGFloat, color: NSColor, thickness: CGFloat = 28) {
    let p = NSBezierPath(); p.move(to: NSPoint(x: x, y: y)); p.line(to: NSPoint(x: x + width, y: y)); stroke(p, color: color, width: thickness)
}
// The globe remains identifiable behind the foreground news page at dock sizes.
let globe = NSRect(x: 397, y: 382, width: 434, height: 434)
stroke(NSBezierPath(ovalIn: globe), color: aqua, width: 26)
stroke(NSBezierPath(ovalIn: NSRect(x: 507, y: 382, width: 214, height: 434)), color: aqua.withAlphaComponent(0.6), width: 16)
stroke(NSBezierPath(ovalIn: NSRect(x: 397, y: 523, width: 434, height: 152)), color: aqua.withAlphaComponent(0.6), width: 16)
// A folded newspaper, rather than tiny text or a generic robot face.
let paper = NSBezierPath(roundedRect: NSRect(x: 207, y: 207, width: 421, height: 492), xRadius: 46, yRadius: 46)
let shadow = NSShadow(); shadow.shadowColor = NSColor.black.withAlphaComponent(0.4)
shadow.shadowBlurRadius = 28; shadow.shadowOffset = NSSize(width: 12, height: -14)
NSGraphicsContext.saveGraphicsState(); shadow.set(); navy.setFill(); paper.fill(); NSGraphicsContext.restoreGraphicsState()
NSGradient(starting: gold, ending: NSColor(red: 0.79, green: 0.55, blue: 0.25, alpha: 1))!.draw(in: paper, angle: -90)
line(272, 614, 235, color: navy, thickness: 43)
line(272, 537, 285, color: navy.withAlphaComponent(0.8), thickness: 24)
let block = NSBezierPath(roundedRect: NSRect(x: 260, y: 323, width: 115, height: 157), xRadius: 15, yRadius: 15)
navy.setFill(); block.fill()
line(412, 463, 143, color: navy.withAlphaComponent(0.85), thickness: 23)
line(412, 402, 143, color: navy.withAlphaComponent(0.7), thickness: 23)
line(412, 341, 105, color: navy.withAlphaComponent(0.55), thickness: 23)
// A single signal node connects the local desk to the wider world.
let node = NSBezierPath(ovalIn: NSRect(x: 746, y: 420, width: 90, height: 90))
aqua.setFill(); node.fill()
stroke(node, color: navy, width: 12)
NSGraphicsContext.restoreGraphicsState()
guard let png = rep.representation(using: .png, properties: [:]) else { fatalError("icon render failed") }
try png.write(to: URL(fileURLWithPath: output))
print("wrote \(output)")
