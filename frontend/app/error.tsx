"use client";

import { useEffect } from "react";

import { AuthLayout } from "@/components/ui/auth-layout";
import { Notice } from "@/components/ui/notice";
import { Btn } from "@/components/ui/btn";
import { Icon } from "@/components/ui/icon";

export default function Error({
  error,
  reset,
}: {
  error: Error;
  reset: () => void;
}) {
  useEffect(() => {
    // Log the error to an error reporting service
    /* eslint-disable no-console */
    console.error(error);
  }, [error]);

  return (
    <AuthLayout
      subtitle="Recarga la página o intenta de nuevo."
      title="Algo salió mal"
    >
      <div className="flex flex-col gap-4">
        <Notice status="danger">
          <span className="flex items-center gap-3">
            <span className="flex text-danger">
              <Icon name="refresh" size={20} />
            </span>
            No pudimos completar la última acción.
          </span>
        </Notice>
        <Btn full icon="refresh" variant="primary" onClick={() => reset()}>
          Reintentar
        </Btn>
      </div>
    </AuthLayout>
  );
}
