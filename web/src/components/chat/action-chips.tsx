"use client";

interface ActionChipsProps {
  chips: string[];
  onChipClick: (text: string) => void;
}

export function ActionChips({ chips, onChipClick }: ActionChipsProps) {
  return (
    <div className="flex flex-wrap gap-1.5 mt-2">
      {chips.map((chip) => (
        <button
          key={chip}
          onClick={() => onChipClick(chip)}
          className="rounded-full border border-teal-200 bg-teal-50 px-3 py-1 text-xs font-medium text-teal-700 transition-colors hover:bg-teal-100 hover:border-teal-300"
        >
          {chip}
        </button>
      ))}
    </div>
  );
}
