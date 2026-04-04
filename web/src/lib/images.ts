export function productImageUrl(productId: string, width = 400, height = 300): string {
  return `https://picsum.photos/seed/${productId}/${width}/${height}`;
}
