"use client";

import { useEffect } from "react";
import { Alert, Button } from "@heroui/react";

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
    <main className="flex min-h-screen items-center justify-center px-6 py-12">
      <div className="flex w-full max-w-sm flex-col gap-5">
        <Alert status="danger">
          <Alert.Content>
            <Alert.Title>Algo salió mal.</Alert.Title>
            <Alert.Description>
              Recarga la página o intenta de nuevo.
            </Alert.Description>
          </Alert.Content>
        </Alert>
        <Button variant="primary" onPress={() => reset()}>
          Reintentar
        </Button>
      </div>
    </main>
  );
}
