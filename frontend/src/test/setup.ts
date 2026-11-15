// jsdom has no ImageData; a minimal stand-in is enough for renderPlane tests.
if (typeof globalThis.ImageData === "undefined") {
  class ImageDataShim {
    constructor(public data: Uint8ClampedArray, public width: number, public height: number) {}
  }
  (globalThis as unknown as { ImageData: unknown }).ImageData = ImageDataShim;
}
