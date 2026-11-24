#ifndef ULAB_PAIRING_H
#define ULAB_PAIRING_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#if defined(_WIN32) && defined(ULAB_PAIRING_EXPORTS)
#define ULAB_PAIRING_API __declspec(dllexport)
#else
#define ULAB_PAIRING_API
#endif

#define ULAB_PAKE_CLIENT 0
#define ULAB_PAKE_SERVER 1
#define ULAB_PAKE_CODE_LEN 6
#define ULAB_PAKE_SECRET_LEN 32
#define ULAB_PAKE_MAX_MESSAGE 512

/* Each context belongs to one pairing attempt and must be freed by its caller. */
ULAB_PAIRING_API void *ulab_pake_new(int role, const uint8_t *code, size_t code_len);
/* Round is 1 or 2. Read and write can occur in either order within a round. */
ULAB_PAIRING_API int ulab_pake_write(void *context, int round, uint8_t *buf,
                                    size_t size, size_t *written);
ULAB_PAIRING_API int ulab_pake_read(void *context, int round, const uint8_t *buf, size_t size);
/* The output needs separate application-level key confirmation before use. */
ULAB_PAIRING_API int ulab_pake_derive(void *context, uint8_t out[ULAB_PAKE_SECRET_LEN]);
ULAB_PAIRING_API void ulab_pake_free(void *context);

#ifdef __cplusplus
}
#endif

#endif
