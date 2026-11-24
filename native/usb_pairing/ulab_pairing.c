#include "ulab_pairing.h"

#include <stdlib.h>

#include "mbedtls/ctr_drbg.h"
#include "mbedtls/ecjpake.h"
#include "mbedtls/entropy.h"
#include "mbedtls/platform_util.h"

struct pairing_context {
    mbedtls_entropy_context entropy;
    mbedtls_ctr_drbg_context random;
    mbedtls_ecjpake_context exchange;
    unsigned progress;
};

enum pairing_progress {
    WROTE_ROUND_ONE = 1,
    READ_ROUND_ONE = 2,
    WROTE_ROUND_TWO = 4,
    READ_ROUND_TWO = 8,
    ROUND_ONE = WROTE_ROUND_ONE | READ_ROUND_ONE,
    BOTH_ROUNDS = ROUND_ONE | WROTE_ROUND_TWO | READ_ROUND_TWO,
    FINISHED = 16,
};

void ulab_pake_free(void *handle)
{
    struct pairing_context *context = handle;
    if (context == NULL) {
        return;
    }
    mbedtls_ecjpake_free(&context->exchange);
    mbedtls_ctr_drbg_free(&context->random);
    mbedtls_entropy_free(&context->entropy);
    mbedtls_platform_zeroize(context, sizeof(*context));
    free(context);
}

void *ulab_pake_new(int role, const uint8_t *code, size_t code_len)
{
    const unsigned char personalization[] = "USBLinkLab PAKE";
    if ((role != ULAB_PAKE_CLIENT && role != ULAB_PAKE_SERVER) ||
        code == NULL || code_len != ULAB_PAKE_CODE_LEN) {
        return NULL;
    }
    for (size_t i = 0; i < code_len; ++i) {
        if (code[i] < '0' || code[i] > '9') {
            return NULL;
        }
    }

    struct pairing_context *context = calloc(1, sizeof(*context));
    if (context == NULL) {
        return NULL;
    }
    mbedtls_entropy_init(&context->entropy);
    mbedtls_ctr_drbg_init(&context->random);
    mbedtls_ecjpake_init(&context->exchange);

    mbedtls_ecjpake_role exchange_role = MBEDTLS_ECJPAKE_CLIENT;
    if (role == ULAB_PAKE_SERVER) {
        exchange_role = MBEDTLS_ECJPAKE_SERVER;
    }
    if (mbedtls_ctr_drbg_seed(&context->random, mbedtls_entropy_func, &context->entropy,
                              personalization, sizeof(personalization) - 1) != 0 ||
        mbedtls_ecjpake_setup(&context->exchange, exchange_role, MBEDTLS_MD_SHA256,
                             MBEDTLS_ECP_DP_SECP256R1, code, code_len) != 0) {
        ulab_pake_free(context);
        return NULL;
    }
    return context;
}

static int can_exchange(const struct pairing_context *context, int round,
                        unsigned required, unsigned operation)
{
    return context != NULL && (round == 1 || round == 2) &&
           (context->progress & FINISHED) == 0 &&
           (context->progress & required) == required &&
           (context->progress & operation) == 0;
}

static int record_result(struct pairing_context *context, int result, unsigned operation)
{
    if (result != 0) {
        context->progress = FINISHED;
        return -1;
    }
    context->progress |= operation;
    return 0;
}

int ulab_pake_write(void *handle, int round, uint8_t *buf, size_t size, size_t *written)
{
    struct pairing_context *context = handle;
    const unsigned required = round == 1 ? 0 : ROUND_ONE;
    const unsigned operation = round == 1 ? WROTE_ROUND_ONE : WROTE_ROUND_TWO;
    if (written == NULL) {
        return -1;
    }
    *written = 0;
    if (buf == NULL || size == 0 || size > ULAB_PAKE_MAX_MESSAGE ||
        !can_exchange(context, round, required, operation)) {
        return -1;
    }

    size_t output_length = 0;
    int result;
    if (round == 1) {
        result = mbedtls_ecjpake_write_round_one(&context->exchange, buf, size, &output_length,
                                                 mbedtls_ctr_drbg_random, &context->random);
    } else {
        result = mbedtls_ecjpake_write_round_two(&context->exchange, buf, size, &output_length,
                                                 mbedtls_ctr_drbg_random, &context->random);
    }
    if (record_result(context, result, operation) != 0) {
        return -1;
    }
    *written = output_length;
    return 0;
}

int ulab_pake_read(void *handle, int round, const uint8_t *buf, size_t size)
{
    struct pairing_context *context = handle;
    const unsigned required = round == 1 ? 0 : ROUND_ONE;
    const unsigned operation = round == 1 ? READ_ROUND_ONE : READ_ROUND_TWO;
    if (buf == NULL || size == 0 || size > ULAB_PAKE_MAX_MESSAGE ||
        !can_exchange(context, round, required, operation)) {
        return -1;
    }

    int result;
    if (round == 1) {
        result = mbedtls_ecjpake_read_round_one(&context->exchange, buf, size);
    } else {
        result = mbedtls_ecjpake_read_round_two(&context->exchange, buf, size);
    }
    return record_result(context, result, operation);
}

int ulab_pake_derive(void *handle, uint8_t out[ULAB_PAKE_SECRET_LEN])
{
    struct pairing_context *context = handle;
    if (context == NULL || context->progress != BOTH_ROUNDS || out == NULL) {
        return -1;
    }
    context->progress = FINISHED;
    size_t size = 0;
    if (mbedtls_ecjpake_derive_secret(&context->exchange, out, ULAB_PAKE_SECRET_LEN, &size,
                                     mbedtls_ctr_drbg_random, &context->random) != 0 ||
        size != ULAB_PAKE_SECRET_LEN) {
        mbedtls_platform_zeroize(out, ULAB_PAKE_SECRET_LEN);
        return -1;
    }
    return 0;
}
