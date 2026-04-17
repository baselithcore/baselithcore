interface Props {
  height?: number | string;
  width?: number | string;
  rounded?: boolean;
}

export function Skeleton({ height = 80, width = "100%" }: Props) {
  return (
    <div
      className="skel"
      style={{
        height: typeof height === "number" ? `${height}px` : height,
        width: typeof width === "number" ? `${width}px` : width,
      }}
    />
  );
}
