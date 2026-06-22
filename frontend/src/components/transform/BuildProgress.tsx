/**
 * BuildProgress — DAG-step progress indicator for lineage builds.
 *
 * Shows a vertical pipeline of build steps with animated transitions
 * between states: pending → running → complete.
 */
import { motion } from 'framer-motion';
import { Check, Loader2, Circle, ExternalLink } from 'lucide-react';
import type { BuildJobStatus } from '../../api/transform';

interface BuildProgressProps {
  status: BuildJobStatus | null;
}

export default function BuildProgress({ status }: BuildProgressProps) {
  if (!status) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-center">
          <div className="w-8 h-8 border-2 border-purple-400 border-t-transparent rounded-full animate-spin mx-auto mb-3" />
          <p className="text-sm text-gray-400">Submitting build job…</p>
        </div>
      </div>
    );
  }

  const {
    steps,
    current_step,
    current_step_name,
    progress_pct,
    is_complete,
    is_success,
    state_message,
    run_page_url,
  } = status;

  return (
    <div className="flex flex-col items-center justify-center h-full px-6 py-8">
      <div className="w-full max-w-sm">
        {/* Header */}
        <div className="text-center mb-6">
          <h4 className="text-sm font-medium text-white mb-1">
            {is_complete
              ? is_success
                ? 'Build Complete'
                : 'Build Failed'
              : 'Building Transformation Lineage'
            }
          </h4>
          <p className="text-xs text-gray-400">
            {is_complete
              ? is_success
                ? 'Loading results…'
                : state_message || 'The build job encountered an error.'
              : current_step_name
            }
          </p>
        </div>

        {/* Progress bar */}
        <div className="w-full h-1.5 bg-gray-800 rounded-full overflow-hidden mb-6">
          <motion.div
            className={`h-full rounded-full ${
              is_complete && !is_success ? 'bg-red-500' : 'bg-purple-500'
            }`}
            initial={{ width: 0 }}
            animate={{ width: `${progress_pct}%` }}
            transition={{ duration: 0.5, ease: 'easeOut' }}
          />
        </div>

        {/* Step list */}
        <div className="space-y-2">
          {steps.map((step, idx) => {
            let state: 'done' | 'active' | 'pending' = 'pending';
            if (idx < current_step) state = 'done';
            else if (idx === current_step && !is_complete) state = 'active';
            else if (is_complete && is_success) state = 'done';
            else if (is_complete && !is_success && idx <= current_step) state = 'done';

            return (
              <div
                key={step}
                className={`flex items-center gap-3 px-3 py-1.5 rounded-md transition-colors ${
                  state === 'active' ? 'bg-purple-900/30' : ''
                }`}
              >
                {/* Icon */}
                <div className="w-5 h-5 flex items-center justify-center flex-shrink-0">
                  {state === 'done' && (
                    <motion.div
                      initial={{ scale: 0 }}
                      animate={{ scale: 1 }}
                      transition={{ type: 'spring', stiffness: 300 }}
                    >
                      <Check className="w-4 h-4 text-green-400" />
                    </motion.div>
                  )}
                  {state === 'active' && (
                    <Loader2 className="w-4 h-4 text-purple-400 animate-spin" />
                  )}
                  {state === 'pending' && (
                    <Circle className="w-3 h-3 text-gray-600" />
                  )}
                </div>

                {/* Label */}
                <span
                  className={`text-xs ${
                    state === 'done'
                      ? 'text-gray-400'
                      : state === 'active'
                        ? 'text-white font-medium'
                        : 'text-gray-600'
                  }`}
                >
                  {step}
                </span>
              </div>
            );
          })}
        </div>

        {/* Job link */}
        {run_page_url && (
          <div className="mt-4 text-center">
            <a
              href={run_page_url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-[10px] text-purple-400 hover:text-purple-300 transition-colors"
            >
              View job run <ExternalLink className="w-3 h-3" />
            </a>
          </div>
        )}
      </div>
    </div>
  );
}
