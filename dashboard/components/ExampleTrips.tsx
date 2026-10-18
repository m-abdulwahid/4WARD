"use client";

export type ExampleTrip = {
  title: string;
  situation: string;
  prompt: string;
  /** Prefills the deadline field when set (prompt already contains the time in words). */
  deadline?: string;
};

// Example trips for judges. Each one was run end to end and resolves to built-in Toronto places.
export const EXAMPLE_TRIPS: ExampleTrip[] = [
  {
    title: "Oshawa to King & Bay by 8:30",
    situation: "Car + bags, park near GO, no full fare, no Presto",
    prompt:
      "I'm in Oshawa with my car and I need to be at King and Bay in downtown Toronto by 8:30 AM. I don't want to drive downtown or pay for parking there, and everything I need is walkable from Union Station. I'd like to keep my extra stuff in the car for later, and I don't want to pay the full GO fare from Oshawa. I have two bags and no Presto card. Find me the best way to get there.",
    deadline: "8:30 AM",
  },
  {
    title: "Late with luggage",
    situation: "Deadline at 6pm, 2 suitcases, no Presto card",
    prompt: "I'm at Kipling Station and need to reach Union Station by 6pm. I have 2 suitcases and no Presto card.",
  },
  {
    title: "Student on a budget",
    situation: "Only $5 to spend, cheapest way across town",
    prompt: "I'm a student at York University going to Kensington Market. Cheapest way please, my budget is $5.",
  },
  {
    title: "Green with an e-bike",
    situation: "Has an e-bike, wants the lowest-emission trip",
    prompt: "From Kensington Market to the Distillery District. I have an e-bike and want the greenest way there.",
  },
];

/** Cards that fill the trip box (they never start a run, so the text can be edited first). */
export default function ExampleTrips({
  current,
  onPick,
}: {
  current: string;
  onPick: (trip: ExampleTrip) => void;
}) {
  return (
    <div>
      <h2 className="mb-1.5 text-xs font-semibold text-zinc-600" id="examples-heading">
        Or start from an example
      </h2>
      <ul aria-labelledby="examples-heading" className="grid grid-cols-1 gap-2 min-[360px]:grid-cols-2">
        {EXAMPLE_TRIPS.map((x, i) => {
          const on = current.trim() === x.prompt;
          return (
            <li key={x.title}>
              <button
                type="button"
                data-testid={`example-${i + 1}`}
                aria-pressed={on}
                title={x.prompt}
                onClick={() => onPick(x)}
                className={`flex h-full min-h-[4.5rem] w-full flex-col items-start gap-0.5 rounded-xl px-3 py-2.5 text-left ring-1 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-zinc-900 ${on ? "bg-zinc-900 text-white ring-zinc-900" : "bg-white text-zinc-900 ring-zinc-200 hover:bg-zinc-50"}`}
              >
                <span className="line-clamp-2 text-sm font-semibold leading-snug">{x.title}</span>
                <span className={`line-clamp-2 text-xs leading-snug ${on ? "text-zinc-300" : "text-zinc-600"}`}>{x.situation}</span>
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
