# Agent News Bot icon

Original globe-and-newspaper artwork in the app palette: navy, gold and aqua.
Created deterministically with AppKit in `draw_icon.swift`, without generated bitmap
services, external images, fonts or third-party trademarks. Licensed with this
repository under MIT.

`AppIcon-1024.png` is the checked-in master. The build renders the same source and
converts it into all standard macOS ICNS sizes. The PNG can be used for the Linux
package and GitHub release artwork. Transparent margins are intentional.

Inspected at 1024, 64 and 32 pixels during the November 27 repair pass. The 256-pixel Linux package asset at `linux/agent_newsbot/resources/app-icon.png` was inspected separately. It is included in the Python package; the Linux service has no desktop GUI.
