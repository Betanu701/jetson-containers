#---
# name: auto_awq:builder
# group: llm
# config: config.py
# requires: '>=36'
# depends: [transformers]
# test: test.py
#---
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

ARG AUTOAWQ_BRANCH \
    AUTOAWQ_CUDA_ARCH

ADD https://api.github.com/repos/casper-hansen/AutoAWQ/git/refs/heads/${AUTOAWQ_BRANCH} /tmp/autoawq_version.json
ADD https://api.github.com/repos/casper-hansen/AutoAWQ_kernels/git/refs/heads/${AUTOAWQ_BRANCH} /tmp/autoawq_kernels_version.json

RUN set -ex \
    && git clone --branch=${AUTOAWQ_BRANCH} --depth=1 https://github.com/casper-hansen/AutoAWQ_kernels /opt/AutoAWQ_kernels \
    && cd /opt/AutoAWQ_kernels \
    && echo "AUTOAWQ_CUDA_ARCH: ${AUTOAWQ_CUDA_ARCH}" \
    && sed "s|{75, 80, 86, 89, 90}|{${AUTOAWQ_CUDA_ARCH}}|g" -i setup.py \
    && python3 setup.py --verbose bdist_wheel --dist-dir /opt \
    \
    && git clone --branch=${AUTOAWQ_BRANCH} --depth=1 https://github.com/casper-hansen/AutoAWQ /opt/AutoAWQ \
    && cd /opt/AutoAWQ \
    && sed -i \
        -e 's|"torch>=*"|"torch"|g' \
        -e 's|"transformers>=*",|"transformers"|g' \
        -e 's|"tokenizers>=*",|"tokenizers"|g' \
        -e 's|"accelerate>=*",|"accelerate"|g' \
        setup.py \
    && python3 setup.py --verbose bdist_wheel --dist-dir /opt \
    \
    && rm -rf \
        /opt/AutoAWQ_kernels \
        /opt/AutoAWQ \
    \
    && pip3 install --no-cache-dir --verbose \
        /opt/autoawq_kernels*.whl \
        /opt/autoawq*.whl \
    \
    && pip3 show autoawq \
    && python3 -c 'import awq'