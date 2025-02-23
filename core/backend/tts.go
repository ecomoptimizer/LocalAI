package backend

import (
	"context"
	"fmt"
	"os"
	"path/filepath"

	config "github.com/go-skynet/LocalAI/core/config"
	"github.com/go-skynet/LocalAI/core/options"
	"github.com/go-skynet/LocalAI/pkg/grpc/proto"
	model "github.com/go-skynet/LocalAI/pkg/model"
	"github.com/go-skynet/LocalAI/pkg/utils"
)

func generateUniqueFileName(dir, baseName, ext string) string {
	counter := 1
	fileName := baseName + ext

	for {
		filePath := filepath.Join(dir, fileName)
		_, err := os.Stat(filePath)
		if os.IsNotExist(err) {
			return fileName
		}

		counter++
		fileName = fmt.Sprintf("%s_%d%s", baseName, counter, ext)
	}
}

func ModelTTS(backend, text, modelFile string, loader *model.ModelLoader, o *options.Option, c config.Config) (string, *proto.Result, error) {
	bb := backend
	if bb == "" {
		bb = model.PiperBackend
	}

	grpcOpts := gRPCModelOpts(c)

	opts := modelOpts(config.Config{}, o, []model.Option{
		model.WithBackendString(bb),
		model.WithModel(modelFile),
		model.WithContext(o.Context),
		model.WithAssetDir(o.AssetsDestination),
		model.WithLoadGRPCLoadModelOpts(grpcOpts),
	})
	piperModel, err := o.Loader.BackendLoader(opts...)
	if err != nil {
		return "", nil, err
	}

	if piperModel == nil {
		return "", nil, fmt.Errorf("could not load piper model")
	}

	if err := os.MkdirAll(o.AudioDir, 0755); err != nil {
		return "", nil, fmt.Errorf("failed creating audio directory: %s", err)
	}

	fileName := generateUniqueFileName(o.AudioDir, "piper", ".wav")
	filePath := filepath.Join(o.AudioDir, fileName)

	// If the model file is not empty, we pass it joined with the model path
	modelPath := ""
	if modelFile != "" {
		if bb != model.TransformersMusicGen {
			modelPath = filepath.Join(o.Loader.ModelPath, modelFile)
			if err := utils.VerifyPath(modelPath, o.Loader.ModelPath); err != nil {
				return "", nil, err
			}
		} else {
			modelPath = modelFile
		}
	}

	res, err := piperModel.TTS(context.Background(), &proto.TTSRequest{
		Text:  text,
		Model: modelPath,
		Dst:   filePath,
	})

	return filePath, res, err
}
